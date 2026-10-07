# The Book of Xellarant

Additional content for Aurora, by [Xellarant](https://github.com/Xellarant).

- [Book content index](https://raw.githubusercontent.com/Xellarant/the-book-of-xellarant/master/the-book-of-xellarant.index): The Book of Xellarant, The Book of Beasts, and Eberron: Forge of the Artificer.
- [Optional Aurora sources index](https://raw.githubusercontent.com/Xellarant/the-book-of-xellarant/master/aurora-sources.index): AuroraLegacy and additional community sources. This index deliberately does not include the Book index.

Use one installation of each collection. The Book expects Aurora's core/internal definitions and the 2014 Player's Handbook; Eberron: Forge of the Artificer also requires the 2024 Player's Handbook spells and origin feats. Enable the appropriate sources in your character's source selection. The optional sources index provides these dependencies, but is unnecessary if you already maintain them separately.

## Upgrading an existing aggregate installation

Version 1.1 of `aurora-sources.index` stops downloading the archived Aurora Builder core, supplements, and Unearthed Arcana alongside AuroraLegacy's maintained copies. Loading both versions can cause the same content IDs to overwrite one another.

Updating an index does not automatically remove its old downloaded files. Back up your custom content, then move only the obsolete, automatically downloaded `aurorabuilder-core`, `aurorabuilder-supplements`, and `aurorabuilder-unearthed-arcana` folders **and their matching `.index` files** outside Aurora's active custom-content directory. These normally live beneath the folder named after the aggregate index. Confirm the paths against your installation before moving anything; keep AuroraLegacy and any personal edits. A separately installed old core/supplement collection can produce the same overlap and also needs review. Reload or reimport the content afterward. This repository does not delete installed files.

## Automation limits

The XML contains both rules and sheet reminders. Some features still require manual tracking:

- **Replicate Magic Item:** choose plans in the builder, record the specific item for a category plan, and add/remove created items in inventory. Armor models provide sheet calculations; create the corresponding inventory attacks yourself.
- **Potent Dragonmark:** prepared spells and its ability increase are represented. Track its restricted extra spell slot separately; it is not an unrestricted class slot.
- **Revised Beast Conclave:** companion choices, additional skills, saving throws, and ability increases are represented. Track added Hit Dice/hit points, remove Multiattack in play, and adjust printed attack text after companion ability changes. Keep companion ability scores at or below 20.
- **Companion proficiency:** the affected Steel Defender and drake saving throws use the character's proficiency directly. Some Legacy screens still display the creature template's proficiency in their generic header. Steel Bond's general ability checks and other situational bonuses may need a manual roll even when skill/save totals are available.
- **Innate and feat spells:** named casting profiles and acquisition metadata are provided. Free-use counters and spell-slot permissions depend on the application version; Legacy may require tracking uses from the feature text.
- **Mixed editions:** older Artificer subclasses remain available through the shared subclass category. Choose the intended source. The beta/2014 Aberrant Dragonmark combination is not reliably excluded in both selection orders by older external content; follow the prerequisite against combining marks.
- **Existing customization:** the optional background-feature choices retain their prior additive behavior. Tool proficiency replacements, situational bonuses, spell components, item lifetimes, and other play-dependent effects are not all automated.

The 2026-10-06 repair pass checked file structure, dependency references, choice pools, progression calculations, creature metadata, and the local Aurora parser. It did not certify every sentence against the complete published Eberron book or exercise every character in the native applications. Homunculus Servant's inline stat block and detailed Eldritch Cannon rules still warrant comparison with the original book.

## Content maintenance

Run the focused validator with Python 3.10 or later:

```powershell
python tools/validate_content.py --corpus 'C:\path\to\Aurora\custom' --engine-core 'C:\path\to\Aurora.Logic\Resources\Data\core' --baseline-ref HEAD
python tools/repair_creatures.py --srd-json 'C:\path\to\5e-SRD-Monsters.json'
```

The validator's dependency arguments are optional; omit them for repository-only checks. Include the engine's embedded core directory when checking all grants, since some internal IDs are supplied by the application rather than downloaded content. `--baseline-ref` is optional and checks ID preservation and version increments against that Git revision.

The creature tool checks without writing by default. Use `--write` only to restore the supported metadata after regeneration, then run the check again. The older generator in the separate translator repository does not include these repairs. Neither tool changes installed content. Application parser checks and live URL checks are separate from these offline validators.
