"""Check EFA choice behavior against installed first-party Aurora definitions.

Use --revision HEAD to reproduce the pre-fix failures without changing files.
These are content/pool checks, not an interactive character-save test.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--revision', help='Read EFA files from this Git revision')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    catalog = {}
    append_supports = {}
    for path in args.corpus.rglob('*.xml'):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for element in root.findall('element'):
            catalog[element.get('id')] = element
    efa = {}
    for filename in ('efa-class.xml', 'efa-species-feats.xml', 'efa-backgrounds.xml'):
        relative = 'Eberron Forge of the Artificer/' + filename
        raw = (subprocess.check_output(['git', 'show', args.revision + ':' + relative], cwd=repo)
               if args.revision else (repo / relative).read_bytes())
        root = ET.fromstring(raw)
        efa.update({e.get('id'): e for e in root.findall('element')})
        for append in root.findall('append'):
            append_supports.setdefault(append.get('id'), set()).update(t.strip() for t in append.findtext('supports','').split(','))
    catalog.update(efa)
    checks, errors = 0, []

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            errors.append(message)

    def pool(rule):
        if rule is None:
            return set()
        support = rule.get('supports', '')
        if support.startswith('ID_'):
            ids = support.split('|')
            return {id for id in ids if id in catalog and catalog[id].get('type') == rule.get('type')}
        return {id for id,e in catalog.items() if e.get('type') == rule.get('type') and
                (not support or set(support.split('||')).intersection(
                    {tag.strip() for tag in e.findtext('supports', '').split(',')} | append_supports.get(id, set())))}

    # PHB24 ASI is a feat choice. A nested ASI then assigns exactly two +1s.
    rules = efa['ID_EFA_CLASS_FEATURE_ARTIFICER_ASI'].findall('rules/select')
    wizard = catalog['ID_WOTC_PHB24_CLASS_FEATURE_WIZARD_ABILITY_SCORE_IMPROVEMENT'].findall('rules/select')
    expected = [(r.get('type'), r.get('level'), r.get('supports', '')) for r in wizard]
    check([(r.get('type'),r.get('level'),r.get('supports','')) for r in rules] == expected, 'ASI choice behavior must match PHB24 class rules')
    check([r.get('name') for r in rules] == ['Feat (Artificer)', 'Feat (Artificer 8)', 'Feat (Artificer 12)', 'Feat (Artificer 16)'], 'ASI rule names preserve existing saved selections')
    for level in (3,4,7,8,11,12,15,16,19):
        active = [r for r in rules if int(r.get('level','1')) <= level]
        check(len(active) == sum(level >= milestone for milestone in (4,8,12,16)), f'ASI milestones at level {level}')
        for rule in active:
            check('ID_WOTC_PHB24_FEAT_ABILITY_SCORE_IMPROVEMENT' in pool(rule), f'ASI feat in level {level} pool')
    asi_feat = catalog['ID_WOTC_PHB24_FEAT_ABILITY_SCORE_IMPROVEMENT']
    check(asi_feat.findtext('requirements').startswith('[character:4]'), 'ASI feat level gate')
    score_rule = asi_feat.find('rules/select')
    check(score_rule.get('number') == '2' and len(pool(score_rule)) == 6, 'ASI feat offers two increases across six scores')
    check(all(catalog[id].findtext("setters/set[@name='allow duplicate']") == 'true' for id in pool(score_rule)), 'ASI can assign both increases to one score')
    boon = efa['ID_EFA_CLASS_FEATURE_ARTIFICER_EPIC_BOON'].find('rules/select')
    check(boon.get('type') == 'Feat' and boon.get('level') == '19' and not boon.get('supports'), 'Epic Boon also permits other qualifying feats')

    # An already-owned required tool must leave other Artisan tool choices.
    defaults = {
        'ALCHEMIST': ['ALCHEMISTS_SUPPLIES','HERBALISM_KIT'],
        'ARMORER': ['SMITHS_TOOLS'],
        'ARTILLERIST': ['WOODCARVERS_TOOLS'],
        'BATTLE_SMITH': ['SMITHS_TOOLS'],
        'CARTOGRAPHER': ['CALLIGRAPHERS_SUPPLIES','CARTOGRAPHERS_TOOLS'],
    }
    artisans = {id for id,e in catalog.items() if e.get('type') == 'Proficiency' and 'Artisan tools' in [t.strip() for t in e.findtext('supports','').split(',')]}
    check(len(artisans) >= 17, 'dependency supplies Artisan tool pool')
    for subclass,tools in defaults.items():
        feature = efa['ID_EFA_ARCHETYPE_FEATURE_' + subclass + '_TOOLS_OF_THE_TRADE']
        choices = feature.findall("rules/select[@type='Proficiency']")
        check(len(choices) == len(tools), subclass + ': every promised tool/replacement gets one choice')
        for suffix in tools:
            default = 'ID_PROFICIENCY_TOOL_PROFICIENCY_' + suffix
            rule = next((r for r in choices if r.get('default') == default), None)
            options = pool(rule)
            check(options == artisans | {default}, subclass + ': correct options for ' + suffix)
            check(bool(options - {default}), subclass + ': already-owned tool leaves replacements')
            check(rule is not None and rule.get('number','1') == '1', subclass + ': one proficiency per tool')
            check(not any(g.get('id') == default for g in feature.findall('rules/grant')), subclass + ': default is not granted twice')

    human = catalog['ID_WOTC_PHB24_RACE_HUMAN']
    human_asi = human.find("rules/select[@type='Ability Score Improvement']")
    for species in ('CHANGELING','KALASHTAR','KHORAVAR','SHIFTER','WARFORGED'):
        race = efa['ID_EFA_RACE_' + species]
        fallback = race.find("rules/select[@type='Ability Score Improvement']")
        check(fallback is not None and fallback.get('requirements') == human_asi.get('requirements') and pool(fallback) == pool(human_asi), species + ': legacy-background ASI fallback matches PHB24')
        check(fallback is not None and fallback.get('requirements') == '!ID_INTERNAL_GRANTS_BACKGROUND_ASI', species + ': 2024 background suppresses fallback')
        common = race.findall("rules/grant[@type='Language']")
        check(len(common) == 1 and common[0].get('id') == 'ID_LANGUAGE_COMMON' and common[0].get('requirements') == '!ID_WOTC_TCOE_OPTION_CUSTOMIZED_LANGUAGE', species + ': normal Common grant')
        languages = race.findall("rules/select[@type='Language']")
        standard = next((r for r in languages if r.get('supports') == 'Standard'), None)
        custom = next((r for r in languages if r.get('supports') == 'Custom Race Language'), None)
        check(standard is not None and standard.get('number') == '2' and standard.get('requirements') == '!ID_WOTC_TCOE_OPTION_CUSTOMIZED_LANGUAGE', species + ': two standard languages')
        check(custom is not None and custom.get('number') == '3' and custom.get('requirements') == 'ID_WOTC_TCOE_OPTION_CUSTOMIZED_LANGUAGE', species + ': mutually exclusive customization')
        check(len(pool(standard) - {'ID_LANGUAGE_COMMON'}) >= 2, species + ': sufficient standard choices')

    dreams = efa['ID_EFA_RACIAL_TRAIT_KALASHTAR_SEVERED_FROM_DREAMS'].find('rules/select')
    skills = {id for id,e in catalog.items() if e.get('type') == 'Proficiency' and 'Skill' in [t.strip() for t in e.findtext('supports','').split(',')]}
    check(dreams is not None and dreams.get('number','1') == '1' and pool(dreams) == skills and len(skills) >= 18, 'Severed from Dreams offers the standard skills and enabled skill extensions')
    archaeologist = efa['ID_EFA_BACKGROUND_ARCHAEOLOGIST'].find("rules/select[@type='Feat']")
    charlatan = catalog['ID_WOTC_PHB24_BACKGROUND_CHARLATAN'].find("rules/select[@type='Feat']")
    check(archaeologist is not None and pool(archaeologist) == pool(charlatan) and archaeologist.get('default') == charlatan.get('default'), 'Archaeologist Skilled acquisition matches PHB24 backgrounds')
    for error in errors:
        print('FAIL: ' + error)
    print(f'{"FAIL" if errors else "PASS"}: {checks} focused EFA choice checks; {len(errors)} failures ({args.revision or "working tree"}).')
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
