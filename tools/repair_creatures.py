"""Repair/check the scoped SRD 5.1 companion export without reserializing XML.

Usage: python tools/repair_creatures.py --srd-json PATH [--write]
The default is a read-only check. Python standard library only.

Source JSON: 5eApiTranslator/5eApiTranslator/Data/5e-SRD-Monsters.json.
The JSON is an input, never modified; its SHA-256 is reported for reproducibility.
Do not rerun the old XellarantXmlGenerator over the repaired file: it still omits
these fields. This script deliberately fails on unknown usage/legendary schemas.

Authoritative corrections verified in the CC-BY-4.0 SRD 5.1, PDF pages 264,
309, 338, 345 (condition immunities); 286-287 (red dragon recharge/resistance
and legendary budget); 261, 263, 325-326, 334, 348, 350-353 (other budgets):
https://media.dndbeyond.com/compendium-images/srd/5.1/SRD_CC_v5.1.pdf
Legendary actions are a Companion Trait containing the complete action list,
because the supported loaders have no separate legendary-action collection.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
ELEMENT = re.compile(r'(?ms)^\t<element\b.*?^\t</element>')
ABILITIES = dict(zip(('str', 'dex', 'con', 'int', 'wis', 'cha'),
                     ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')))
SKILLS = {
    'acrobatics': 'dexterity', 'animal-handling': 'wisdom', 'arcana': 'intelligence',
    'athletics': 'strength', 'deception': 'charisma', 'history': 'intelligence',
    'insight': 'wisdom', 'intimidation': 'charisma', 'investigation': 'intelligence',
    'medicine': 'wisdom', 'nature': 'intelligence', 'perception': 'wisdom',
    'performance': 'charisma', 'persuasion': 'charisma', 'religion': 'intelligence',
    'sleight-of-hand': 'dexterity', 'stealth': 'dexterity', 'survival': 'wisdom',
}
# The JSON mistakenly repeats Blinded where the SRD specifies Deafened.
DEAFENED_CORRECTIONS = {
    'flying-sword', 'ochre-jelly', 'rug-of-smothering', 'shambling-mound',
    'shrieker', 'violet-fungus',
}
LEGENDARY_NONDRAGONS = {
    'aboleth', 'androsphinx', 'gynosphinx', 'kraken', 'lich', 'mummy-lord',
    'solar', 'tarrasque', 'unicorn', 'vampire-vampire',
    'vampire-bat', 'vampire-mist',
}
COLORS = {'black', 'blue', 'brass', 'bronze', 'copper', 'gold', 'green', 'red', 'silver', 'white'}
LEGENDARY_BUDGETS = {name: 3 for name in LEGENDARY_NONDRAGONS}
LEGENDARY_BUDGETS.update({f'{age}-{color}-dragon': 3
                         for age in ('adult', 'ancient') for color in COLORS})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def part(value):
    return re.sub(r'[^A-Z0-9]+', '_', value.upper()).strip('_')


def companion_id(monster):
    return 'ID_XELLARANT_COMPANION_' + part(monster['index'])


def child_ids(monster, category, items):
    seen = set()
    result = []
    for item in items:
        base = f'ID_XELLARANT_COMPANION_{category}_{part(monster["index"])}_{part(item["name"])}'
        name, suffix = base, 2
        while name in seen:
            name, suffix = f'{base}_{suffix}', suffix + 1
        seen.add(name)
        result.append(name)
    return result


def setters(element):
    return {node.get('name'): node.text or '' for node in element.findall('setters/set')}


def usage_text(usage):
    if usage['type'] == 'per day':
        return f'{int(usage["times"])}/Day'
    if usage['type'] == 'recharge on roll':
        require(usage['dice'] == '1d6', f'Unreviewed recharge die: {usage}')
        minimum = int(usage['min_value'])
        require(1 <= minimum <= 6, f'Invalid recharge: {usage}')
        return 'Recharge ' + (str(minimum) if minimum == 6 else f'{minimum}\u20136')
    if usage['type'] == 'recharge after rest':
        require(usage['rest_types'] == ['short', 'long'], f'Unreviewed rest usage: {usage}')
        return '1/Short or Long Rest'
    raise ValueError(f'Unreviewed usage: {usage}')


def corrected_conditions(monster):
    names = [x['name'] for x in monster.get('condition_immunities', [])]
    if monster['index'] in DEAFENED_CORRECTIONS and names.count('Blinded') == 2:
        names[names.index('Blinded', names.index('Blinded') + 1)] = 'Deafened'
    require(len(names) == len(set(names)), f'Duplicate conditions: {monster["name"]}')
    return ', '.join(names)


def proficiency_key(item):
    index = item['proficiency']['index']
    if index.startswith('saving-throw-'):
        ability = ABILITIES[index.removeprefix('saving-throw-')]
        return ability + ':save', ability
    require(index.startswith('skill-'), f'Unexpected proficiency {index}')
    skill = index.removeprefix('skill-')
    return skill.replace('-', ' '), SKILLS[skill]


def supplemental_stats(monster):
    rules = []
    for movement in ('fly', 'climb', 'swim', 'burrow'):
        if movement in monster['speed']:
            value = monster['speed'][movement]
            require(re.fullmatch(r'\d+ ft\.', value) is not None, f'Unreviewed speed {value}')
            rules.append((f'companion:speed:{movement}', value.split()[0], 'base'))
    pb = int(monster['proficiency_bonus'])
    for item in monster.get('proficiencies', []):
        key, ability = proficiency_key(item)
        modifier = (int(monster[ability]) - 10) // 2
        bonus = int(item['value']) - modifier
        # Distinct bonus groups follow baseline Aurora companion expertise rules.
        rules.append((f'companion:{key}:proficiency', 'companion:proficiency', 'base'))
        if bonus == 2 * pb:
            rules.append((f'companion:{key}:proficiency', 'companion:proficiency', 'double'))
        elif bonus != pb:
            rules.append((f'companion:{key}:misc', str(bonus - pb), ''))
    return rules


def set_value(block, key, value):
    pattern = re.compile(r'(?m)^\t\t\t<set name="' + re.escape(key) + r'">.*?</set>$')
    replacement = f'\t\t\t<set name="{key}">{escape(value)}</set>'
    if pattern.search(block):
        return pattern.sub(lambda _: replacement, block)
    return block.replace('\t\t</setters>', replacement + '\n\t\t</setters>', 1)


def append_reference(block, key, new_id):
    existing = setters(ET.fromstring(block)).get(key, '').split(',')
    existing = [x.strip() for x in existing if x.strip()]
    if new_id not in existing:
        existing.append(new_id)
    return set_value(block, key, ','.join(existing))


def add_usage(block, usage):
    label = usage_text(usage)
    paragraph = f'\t\t\t<p class="usage">Usage: {escape(label)}.</p>\n'
    if paragraph not in block:
        block = block.replace('\t\t<description>\n', '\t\t<description>\n' + paragraph, 1)
    sheet = ET.fromstring(block).find('sheet')
    require(sheet is not None and sheet.find('description') is not None, 'Missing original sheet description')
    if sheet.get('usage') is None:
        block = block.replace('<sheet>', f'<sheet usage={quoteattr(label)}>', 1)
    else:
        require(sheet.get('usage') == label, f'Conflicting usage {sheet.get("usage")} / {label}')
    # Companion export appends sheet.usage to the name. Keep its prose untouched.
    return block


def child_block(name, kind, element_id, paragraphs, action=None):
    sheet_attribute = f' action={quoteattr(action)}' if action else ''
    description = '\n'.join('\t\t\t<p>' + escape(text) + '</p>' for text in paragraphs)
    sheet_text = '\n'.join(escape(text) for text in paragraphs)
    return (f'\t<element name={quoteattr(name)} type={quoteattr(kind)} source="The Book of Beasts" id={quoteattr(element_id)}>\n'
            '\t\t<compendium display="false" />\n\t\t<description>\n' + description +
            f'\n\t\t</description>\n\t\t<sheet{sheet_attribute}>\n'
            f'\t\t\t<description>{sheet_text}</description>\n\t\t</sheet>\n\t</element>')


def legendary_paragraphs(monster):
    budget = LEGENDARY_BUDGETS[monster['index']]  # Fail on an unreviewed creature.
    overview = (f'This creature can take {budget} legendary actions, choosing from the options below. '
                "Only one option can be used at a time and only at the end of another creature's turn. "
                'It regains spent legendary actions at the start of its turn.')
    return [overview] + [x['name'] + '. ' + x['desc'] for x in monster['legendary_actions']]


def repair(text, source):
    blocks = {ET.fromstring(m.group()).get('id'): m.group() for m in ELEMENT.finditer(text)}
    originals = set(blocks)
    monsters = [source[ET.fromstring(block).get('name')] for block in blocks.values()
                if ET.fromstring(block).get('type') == 'Companion']
    require(len(monsters) == 273, 'Expected the original 273 companions; review any expansion first')
    added = {}
    for monster in monsters:
        element_id = companion_id(monster)
        require(element_id in blocks, f'Unexpected companion ID for {monster["name"]}')
        block = blocks[element_id]
        conditions = corrected_conditions(monster)
        if conditions:
            block = set_value(block, 'condition-immunities', conditions)
            block = set_value(block, 'conditionImmunities', conditions)
        existing_rules = [tuple(node.get(k, '') for k in ('name', 'value', 'bonus'))
                          for node in ET.fromstring(block).findall('rules/stat')]
        for name, value, bonus in supplemental_stats(monster):
            rule = (name, value, bonus)
            if rule in existing_rules:
                continue
            require(not any(r[0] == name and r[2] == bonus for r in existing_rules),
                    f'Conflicting stat: {monster["name"]} {name}/{bonus}')
            suffix = f' bonus="{bonus}"' if bonus else ''
            line = f'\t\t\t<stat name={quoteattr(name)} value={quoteattr(value)}{suffix} />\n'
            block = block.replace('\t\t</rules>', line + '\t\t</rules>', 1)
        for category, key in (('TRAIT', 'special_abilities'), ('ACTION', 'actions')):
            items = monster.get(key, [])
            for item, child_id in zip(items, child_ids(monster, category, items)):
                require(child_id in blocks, f'Missing original child {child_id}')
                if item.get('usage'):
                    blocks[child_id] = add_usage(blocks[child_id], item['usage'])
        reactions = monster.get('reactions', [])
        for item, child_id in zip(reactions, child_ids(monster, 'REACTION', reactions)):
            block = append_reference(block, 'reactions', child_id)
            generated = child_block(item['name'], 'Companion Reaction', child_id, [item['desc']], 'Reaction')
            if child_id in blocks:
                require(blocks[child_id] == generated, f'Existing reaction changed: {child_id}')
            else:
                added[child_id] = generated
        if monster.get('legendary_actions'):
            child_id = f'ID_XELLARANT_COMPANION_TRAIT_{part(monster["index"])}_LEGENDARY_ACTIONS'
            block = append_reference(block, 'traits', child_id)
            generated = child_block('Legendary Actions', 'Companion Trait', child_id,
                                    legendary_paragraphs(monster), 'Legendary Action')
            if child_id in blocks:
                require(blocks[child_id] == generated, f'Existing legendary trait changed: {child_id}')
            else:
                added[child_id] = generated
        blocks[element_id] = block
    result = ELEMENT.sub(lambda m: blocks[ET.fromstring(m.group()).get('id')], text)
    if added:
        addition = '\n\t<!-- SRD reactions and legendary actions retained for all supported companion loaders. -->\n'
        result = result.replace('</elements>', addition + '\n'.join(added.values()) + '\n</elements>', 1)
    if result != text:
        result = result.replace('<update version="0.0.2">', '<update version="0.0.3">', 1)
    require(originals <= {node.get('id') for node in ET.fromstring(result).findall('element')},
            'Repair removed an original element ID')
    return result


def evaluate_stats(element, pb):
    # Loader: untyped bonuses sum; same-name bonus groups keep the maximum.
    # The companion's CR seeds companion:proficiency (StatisticsHandler2).
    values = defaultdict(int)
    groups = defaultdict(list)
    for node in element.findall('rules/stat'):
        raw = node.get('value')
        value = pb if raw == 'companion:proficiency' else int(raw)
        if node.get('bonus'):
            groups[(node.get('name'), node.get('bonus'))].append(value)
        else:
            values[node.get('name')] += value
    for (name, _), choices in groups.items():
        values[name] += max(choices)
    return values


def validate(text, source):
    root = ET.fromstring(text)
    elements = {element.get('id'): element for element in root.findall('element')}
    require(len(elements) == len(root.findall('element')), 'Duplicate element IDs')
    companions = [x for x in elements.values() if x.get('type') == 'Companion']
    require(len(companions) == 273, 'Companion count changed')
    counts = Counter(companions=len(companions), usages=0, reactions=0, legendary_actions=0,
                     legendary_traits=0, condition_aliases=0, special_speeds=0, proficiency_modifiers=0)
    for element in companions:
        monster = source[element.get('name')]
        data = setters(element)
        pb = int(monster['proficiency_bonus'])
        # Confirm the source PB matches the CR-derived seed of both Aurora loaders.
        expected_pb = 2 if monster['challenge_rating'] < 5 else 2 + (int(monster['challenge_rating']) - 1) // 4
        require(pb == expected_pb, f'CR/PB mismatch: {monster["name"]}')
        stats = evaluate_stats(element, pb)
        for ability in ABILITIES.values():
            require(int(data[ability]) == monster[ability], f'Ability mismatch: {monster["name"]}/{ability}')
        require(stats['companion:hp:max'] == monster['hit_points'], f'HP mismatch: {monster["name"]}')
        require(stats['companion:ac'] == int(re.match(r'\d+', data['ac']).group()), f'AC mismatch: {monster["name"]}')
        for movement in ('walk', 'fly', 'climb', 'swim', 'burrow'):
            expected = int(monster['speed'].get(movement, '0 ft.').split()[0])
            key = 'companion:speed' + ('' if movement == 'walk' else ':' + movement)
            require(stats[key] == expected, f'Speed mismatch: {monster["name"]}/{movement}')
            counts['special_speeds'] += int(movement != 'walk' and movement in monster['speed'])
        for item in monster.get('proficiencies', []):
            key, ability = proficiency_key(item)
            actual = (int(data[ability]) - 10) // 2 + stats[f'companion:{key}:proficiency'] + stats[f'companion:{key}:misc']
            require(actual == int(item['value']), f'Calculated modifier mismatch: {monster["name"]}/{key}')
            counts['proficiency_modifiers'] += 1
        conditions = corrected_conditions(monster)
        if conditions:
            require(data.get('condition-immunities') == data.get('conditionImmunities') == conditions,
                    f'Condition compatibility mismatch: {monster["name"]}')
            counts['condition_aliases'] += 1
        for setter, kind in (('traits', 'Companion Trait'), ('actions', 'Companion Action'), ('reactions', 'Companion Reaction')):
            ids = [x.strip() for x in data.get(setter, '').split(',') if x.strip()]
            require(len(ids) == len(set(ids)), f'Duplicate references: {monster["name"]}/{setter}')
            for element_id in ids:
                require(element_id in elements and elements[element_id].get('type') == kind,
                        f'Invalid {setter} reference: {element_id}')
        for category, key in (('TRAIT', 'special_abilities'), ('ACTION', 'actions')):
            items = monster.get(key, [])
            for item, element_id in zip(items, child_ids(monster, category, items)):
                node = elements[element_id]
                require(item['desc'] in ''.join(node.find('description').itertext()), f'Original prose lost: {element_id}')
                if item.get('usage'):
                    expected = usage_text(item['usage'])
                    require(node.find('sheet').get('usage') == expected, f'Missing usage: {element_id}')
                    require(f'Usage: {expected}.' in ''.join(node.find('description').itertext()), f'Missing detail usage: {element_id}')
                    require(node.findtext('sheet/description') == item['desc'], f'Original sheet prose changed: {element_id}')
                    counts['usages'] += 1
        items = monster.get('reactions', [])
        for item, element_id in zip(items, child_ids(monster, 'REACTION', items)):
            require(element_id in data.get('reactions', '').split(','), f'Unlinked reaction: {element_id}')
            require(elements[element_id].findtext('sheet/description') == item['desc'], f'Reaction text mismatch: {element_id}')
            counts['reactions'] += 1
        if monster.get('legendary_actions'):
            element_id = f'ID_XELLARANT_COMPANION_TRAIT_{part(monster["index"])}_LEGENDARY_ACTIONS'
            require(element_id in data.get('traits', '').split(','), f'Unlinked legendary trait: {element_id}')
            require(elements[element_id].findtext('sheet/description') == '\n'.join(legendary_paragraphs(monster)), f'Legendary text mismatch: {element_id}')
            counts['legendary_traits'] += 1
            counts['legendary_actions'] += len(monster['legendary_actions'])
    # Independent known-answer examples catch generic mapping/stacking mistakes.
    for name, expected in {
        'Homunculus': {'companion:speed:fly': 40},
        'Ancient Red Dragon': {'companion:perception:proficiency': 14, 'companion:wisdom:save:proficiency': 7},
        'Flying Sword': {'companion:dexterity:save:proficiency': 2},
    }.items():
        values = evaluate_stats(elements[companion_id(source[name])], source[name]['proficiency_bonus'])
        for key, value in expected.items():
            require(values[key] == value, f'Known-answer failure: {name}/{key}')
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--srd-json', required=True, type=Path)
    parser.add_argument('--xml', type=Path, default=ROOT / 'the-book-of-xellarant' / 'creatures.xml')
    parser.add_argument('--write', action='store_true', help='Apply the repair; otherwise validate only')
    args = parser.parse_args()
    source_bytes = args.srd_json.read_bytes()
    source = {monster['name']: monster for monster in json.loads(source_bytes)}
    original_bytes = args.xml.read_bytes()
    bom = original_bytes.startswith(b'\xef\xbb\xbf')
    newline = '\r\n' if b'\r\n' in original_bytes else '\n'
    original = original_bytes.decode('utf-8-sig').replace('\r\n', '\n')
    repaired = repair(original, source)
    if not args.write:
        require(repaired == original, 'File requires repair. Review and run again with --write.')
    counts = validate(repaired, source)
    require(repair(repaired, source) == repaired, 'Repair is not idempotent')
    if args.write and repaired != original:
        data = repaired.replace('\n', newline).encode('utf-8')
        args.xml.write_bytes((b'\xef\xbb\xbf' if bom else b'') + data)
    print(json.dumps({'mode': 'write' if args.write else 'check', 'changed': repaired != original,
                      'source_sha256': hashlib.sha256(source_bytes).hexdigest(), **counts,
                      'idempotent': True}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, ET.ParseError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
