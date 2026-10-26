# Change Log

## 0.4.0

### ⚠️ Breaking
- **Add-on id** — the add-on is now installed as `re_gpuc_editor` (was `ree_gpuc_editor`). **Uninstall the old version before installing this one, or both will be loaded.** Saved presets are kept.

### New
- **Weld Parts** — two Parts made from bones can be welded along the bones they share. Welds follow the Parts when they are regenerated, are exported, and come back with Rebuild from Bones and Update Existing. A weld that cannot be exported is skipped, and the export message and the Weld panel say why.

### Changed
- **Export** — no longer needs a template `.gpuc`; the Template field is gone from the export dialog. Wilds-Dynamics can build a cloth before it has been exported once.
- **Export** — each driven joint now follows every particle skinned to its bone, as the game's own cloth does. Cloth with extra particles between bones exports differently.
- **Header** — Unkn Param 5 is now **Friction** (effect unknown). **Its value in existing .blend files resets to 0.**
- **Header** — Unkn Param 10 and 11 are removed; the export writes them.
- **Tooltips and messages** — values of unknown effect say so plainly, and error messages say what to do next.

### Fixed
- **Export dialog** — the template file button no longer fails with "cannot activate file selector".
- **Own capsules** — kept on export when the original file has no capsule section, in all games including Keep Structure. Cloth without capsules no longer exports an empty capsule section.
- **Keep Structure without a mesh** — capsule ends and root joints are placed on the reference skeleton instead of the origin, and capsule offsets point the same way as with a mesh. **Re-import cloth imported this way.**

## 0.3.1

### Changed
- **General Tools** — Check All is renamed **Check** and sits beside Create, below Import / Export. It checks every Part of the active cloth.
- **License** — the full GPL-3.0 text is included. The package contains the add-on only.

## 0.3.0

### New
- **Onimusha: Way of the Sword** — import (Keep Structure, Rebuild from Bones), export and new cloth. Files that drive mesh vertices directly cannot be imported, as in Wilds.

### Changed
- **Part Friction** — no longer limited to 1. The slider still stops at 1, and the preview uses at most 1.

### Fixed
- **Own capsules** — kept when exporting Onimusha cloth.

## 0.2.0

### Changed
- **Mapping Range** — changing Min / Max now keeps the vertex group weights and colors and changes the particle values instead (collision radius, limit distances and so on). 0.1.x did the opposite.

## 0.1.10

### Fixed
- **Particle display** — color issues in the interface.

## 0.1.9

### Fixed
- **Particle display** — the particle colors switch to the value being edited when you change Value or Mapping Range, or use Paint / Set on Selected.

## 0.1.8

### Changed
- **Particle values** — adjusted how weights map to values.

## 0.1.7

### Changed
- **Particle layout** — adjusted.

## 0.1.6

### Fixed
- **Installation** — no longer fails with an `_RestrictContext.active_object` error when the add-on is installed or enabled.

## 0.1.5

### New
- **GPUC: Presets** — save and apply your own parameter presets.

## 0.1.3 – 0.1.4

### New
- **Particle offsets** — move generated particles in Edit Mode; positions are saved when you leave it.

## 0.1.2

### Fixed
- **RE4** — Part collision masks are read from the right place, and the value before them is editable on its own. **Re-import RE4 cloth imported with earlier test versions.**

## 0.1.1

- First packaged version, with a test version of RE4 Keep Structure.
