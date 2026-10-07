"""Offline integrity and regression checks for Book XML. Standard library only.

Optional --corpus validates concrete dependencies against an installed collection.
Optional --baseline-ref verifies existing IDs and update versions against Git.
This does not simulate a complete character or certify publisher-text fidelity.
"""
from pathlib import Path
from collections import Counter, defaultdict
import argparse
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--engine-core', type=Path, help='Aurora engine embedded core XML directory')
    parser.add_argument('--baseline-ref')
    args = parser.parse_args()
    repo = args.repo.resolve()
    checks, errors = 0, []

    def expect(ok, message):
        nonlocal checks
        checks += 1
        if not ok:
            errors.append(message)

    trees, local = {}, {}
    for path in sorted([*repo.rglob('*.xml'), *repo.rglob('*.index')]):
        relative = path.relative_to(repo).as_posix()
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
            root = ET.fromstring(text)
        except (UnicodeError, ET.ParseError) as exc:
            errors.append(f'{relative}: {exc}')
            continue
        trees[relative] = root
        expect(root.tag in ('elements', 'index'), relative + ': root')
        expect(not any(marker in text for marker in ('\ufffd', '\u00e2\u20ac', '\u00c3\u00a2')), relative + ': damaged encoding')
        for e in root.findall('element'):
            id = e.get('id')
            expect(all(e.get(a) for a in ('id', 'name', 'type', 'source')), str(id) + ': required header')
            expect(id not in local, str(id) + ': duplicate ID')
            local[id] = e
            expect('requirements' not in e.attrib, str(id) + ': ignored element requirements attribute')
            sheet = e.find('sheet')
            if sheet is not None:
                expect(not (sheet.text or '').strip(), str(id) + ': ignored direct sheet text')
                expect(all(child.tag == 'description' for child in sheet), str(id) + ': sheet children must survive both importers')
        if args.baseline_ref:
            result = subprocess.run(['git', 'show', args.baseline_ref + ':' + relative], cwd=repo, capture_output=True)
            if result.returncode == 0:
                before = ET.fromstring(result.stdout)
                previous_ids = {e.get('id') for e in before.findall('element')}
                expect(previous_ids <= {e.get('id') for e in root.findall('element')}, relative + ': removed saved-character IDs')
                if ET.tostring(before) != ET.tostring(root):
                    v = lambda r: tuple(int(n) for n in r.find('info/update').get('version').split('.'))
                    expect(v(root) > v(before), relative + ': changed content needs version increment')

    catalog = {}
    if args.corpus:
        expect(args.corpus.is_dir(), 'corpus directory exists')
        dependencies = list(args.corpus.rglob('*.xml'))
        if args.engine_core:
            expect(args.engine_core.is_dir(), 'engine core directory exists')
            dependencies.extend(args.engine_core.rglob('*.xml'))
        for path in dependencies:
            # Exclude an installed copy of this same Book, regardless of its folder name.
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            update = root.find('info/update/file')
            if update is not None and '/xellarant/the-book-of-xellarant/' in update.get('url', '').lower():
                continue
            for e in root.findall('element'):
                if e.get('id'):
                    expect(e.get('id') not in local, str(path) + ': external collision ' + e.get('id'))
                    catalog[e.get('id')] = e
    catalog.update(local)
    tags = {id: {s.strip() for s in e.findtext('supports', '').split(',')} for id,e in catalog.items()}

    index = trees['the-book-of-xellarant.index']
    files = index.findall('files/file')
    expect(len({e.get('name') for e in files}) == len(files), 'duplicate index filename')
    expect(len({e.get('url').lower() for e in files}) == len(files), 'duplicate index URL')
    listed = set()
    for f in files:
        path = urllib.parse.unquote(urllib.parse.urlparse(f.get('url')).path).split('/master/', 1)[-1]
        listed.add(path)
        expect(path in trees, 'missing indexed file: ' + path)
        if path in trees:
            update = trees[path].find('info/update/file')
            expect(update is not None and update.get('name') == f.get('name') and update.get('url').lower() == f.get('url').lower(), path + ': self-update mismatch')
    expect(listed == {p for p,t in trees.items() if t.tag == 'elements'}, 'unindexed content')
    aggregate = trees['aurora-sources.index']
    names = {f.get('name') for f in aggregate.findall('files/file')}
    expect('the-book-of-xellarant.index' not in names, 'Book and aggregate must remain separate')
    expect(not names.intersection({'aurorabuilder-core.index', 'aurorabuilder-supplements.index', 'aurorabuilder-unearthed-arcana.index'}), 'duplicate archived/maintained upstream collections')

    if args.corpus:
        for id,e in local.items():
            for rule in e.findall('rules/grant'):
                target = rule.get('id') or rule.get('name')
                if rule.get('type') == 'Grants' and target.startswith('ID_INTERNAL_'):
                    continue  # Some internal markers are embedded in the application.
                expect(target in catalog, id + ': unresolved grant ' + str(target))
                if target in catalog:
                    expect(catalog[target].get('type') == rule.get('type'), id + ': grant type mismatch ' + target)
            for rule in e.findall('rules/select') + e.findall('multiclass/rules/select'):
                supports = rule.get('supports', '')
                if supports.startswith('ID_') and all(re.fullmatch(r'ID_\w+', part) for part in supports.split('|')):
                    for target in supports.split('|'):
                        expect(target in catalog and catalog[target].get('type') == rule.get('type'), id + ': selector target ' + target)
        for root in trees.values():
            for append in root.findall('append'):
                expect(append.get('id') in catalog, 'unresolved append ' + append.get('id'))

    def find(suffix):
        matches = [e for id,e in local.items() if id.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError('Expected one element ending ' + suffix)
        return matches[0]

    def stat_value(elements, name, level, seed):
        rules = [s for e in elements for s in e.findall('rules/stat') if s.get('name') == name and int(s.get('level', '1')) <= level]
        groups = defaultdict(list)
        total = 0
        for r in rules:
            val = r.get('value')
            value = int(val) if re.fullmatch(r'-?\d+', val) else seed[val]
            if r.get('bonus'):
                groups[r.get('bonus')].append(value)
            else:
                total += value
        return total + sum(max([0, *values]) for values in groups.values())

    artificer = local['ID_EFA_CLASS_ARTIFICER']
    spellcasting = local['ID_EFA_CLASS_FEATURE_ARTIFICER_SPELLCASTING']
    replicate = local['ID_EFA_CLASS_FEATURE_ARTIFICER_REPLICATE_MAGIC_ITEM']
    for level,row in enumerate(artificer.findall("description/table[@class='class-features']/tr"), 1):
        cells = [''.join(c.itertext()).strip() for c in row.findall('td')]
        for column,key in [(3,'replicate:plans:known'), (4,'replicate:items:max'), (5,'artificer:cantrips:known'), (6,'artificer:spellcasting:prepare'), *[(6+n,f'artificer:spellcasting:slots:{n}') for n in range(1,6)]]:
            expected = 0 if cells[column] in ('-', '—') else int(cells[column])
            expect(stat_value([replicate,spellcasting], key, level, {}) == expected, f'Artificer {level}: {key} differs from class table')
        choices = sum(int(s.get('number','1')) for s in replicate.findall('rules/select') if int(s.get('level','1')) <= level)
        expect(choices == (0 if cells[3] == '-' else int(cells[3])), f'Artificer {level}: plan choices differ from table')
    expect(len(artificer.findall("description/table[@class='class-features']/tr")) == 20, 'Artificer table covers all 20 levels')
    plans = [e for id,e in local.items() if 'EFA Artificer Plan' in tags[id]]
    expect(Counter(int(re.search(r':(\d+)', e.findtext('requirements')).group(1)) for e in plans) == {2:16,6:22,10:11,14:7}, 'plan pools match all four authored tables')
    expect(sum('EFA Armor Model' in t for t in tags.values()) == 3, 'three armor models')
    for int_mod in (-2,0,1,3,5):
        minimum = stat_value([find('ARTIFICER_TINKERS_MAGIC')], 'intelligence:modifier:min1', 11, {'intelligence:modifier':int_mod})
        uses = stat_value([find('ARTIFICER_SPELL_STORING_ITEM')], 'intelligence:modifier:min1:x2', 11, {'intelligence:modifier:min1':minimum})
        expect(uses == max(2,2*int_mod), f'Spell-Storing uses at INT modifier {int_mod}')
        maps = stat_value([find('CARTOGRAPHER_ADVENTURERS_ATLAS')], 'atlas:targets', 3, {'intelligence:modifier:min1':minimum})
        expect(maps == max(2,1+int_mod), f'Atlas minimum at INT modifier {int_mod}')
    for level in (3,8,9,14,15,20):
        cannon = find('ARTILLERIST_ELDRITCH_CANNON')
        for key,expected in [('cannon:hp',5*level), ('cannon:damage:dice',2+(level>=9)), ('cannon:protector:dice',1+(level>=9)), ('cannon:count',1+(level>=15))]:
            expect(stat_value([cannon],key,level,{'level:artificer':level}) == expected, f'Cannon {level}: {key}')
    avenger = find('CLASSFEATURE_AVENGER_SPELLCASTING')
    for level,cha in [(2,-1),(7,3),(20,5)]:
        rules = avenger.findall('rules/stat')
        seed = {'charisma:modifier':cha,'level:avenger:half':level//2}
        # Resolve the named intermediate used by the authored preparation rule.
        for rule in rules:
            key = rule.get('name')
            if 'prepare' in key and key not in seed:
                seed[key] = stat_value([avenger],key,level,seed)
        expect(seed['avenger:spellcasting:prepare'] == max(1,cha+level//2), f'Avenger prepared count at {level}/{cha}')
    bond = find('BEAST_ANIMAL_COMPANION')
    expect(len(bond.find("rules/select[@type='Companion']").get('supports').split('|')) == 8, 'eight authored Beast companions')
    expect(sum('TBOX Beast Companion Skill' in t for t in tags.values()) == 18, '18 companion skills')
    expect(sum('TBOX Beast Companion Ability Score Increase' in t for t in tags.values()) == 6, 'six companion ASI options')
    expect([(int(s.get('level')),int(s.get('number'))) for s in bond.findall("rules/select[@type='Ability Score Improvement']")] == [(n,2) for n in (4,8,12,16,19)], 'companion ASI milestones')
    for e in [x for x in local.values() if x.get('type') == 'Companion' and ('ID_EFA_' in x.get('id') or 'DRAKE' in x.get('id'))]:
        for s in e.findall('rules/stat'):
            expect(not (s.get('name') == 'companion:proficiency' and s.get('value') == 'proficiency'), e.get('id') + ': CR proficiency would stack with character proficiency')
    for error in errors:
        print('FAIL: ' + error)
    print(f'{"FAIL" if errors else "PASS"}: {checks} checks; {len(trees)} XML/index files; {len(local)} unique content IDs; {len(errors)} failures.')
    print('Offline checks only; full character execution and publisher-text review remain separate.')
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
