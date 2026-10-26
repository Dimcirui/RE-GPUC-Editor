# Change Log

## Unreleased

### New
- **Drive Mesh Vertices** — a Header setting: the cloth moves the mesh's vertices near it directly, in every LOD, instead of its bones, as some Onimusha files do. Experimental for Wilds and RE9. The export reads the vertices from the exported `.mesh` and is refused without it. Which vertices is an export setting (Vertex Selection): By Distance, or Reference Mesh, the vertices picked on the exported `.mesh` imported as it is ("Import Reference Mesh", checked vertex for vertex; Edit Mode tools in GPUC: Driven Vertices; the picks carry over to a new import). Triangles per Vertex sets how many cloth triangles each one follows.

- **Force Export** — in the export dialog, off by default. See below.
- **RE4 cloth from bones** — a cloth created for RE4 now exports as a whole RE4 file, its particles skinned by the exported `.mesh`'s weighted bones, driving joints or (Drive Mesh Vertices) LOD0's vertices. Its colliders are capsules, spheres and planes (below). The Header shows RE4's `+0x44` and `+0x60` as Collision Iterations and Solver Iterations, the Configs' values as Velocity Damping, Blend Translation, Blend Rotation, Simulation Strength, Blend Weight and Gravity Factor, the Part's `+0x30` as Long Range Attachment Stretchiness: all named by guess, Unconfirmed in their tooltips. A new Config starts at the game files' commonest values (0, 0, 0, 1, 1, 1): at 0 the cloth did not move the mesh. A Part made from bones keeps its name, Config, root joint and export settings editable; CLSP flags and curves are hidden. The game list names it RE4 instead of RE4 (Keep Structure).
- **RE4 collision planes and spheres** — Collision From Bones adds a Plane for RE4: an endless plane through the joint facing its +Y (as the game files' ground on `VirtualGround`), with Normal and Distance in the joint's space; it is drawn in the 3D view and written to RE4's planes. An RE4 sphere (a capsule whose two ends meet) is now written as a sphere instead of a capsule; Sphere and Plane Bits name the cloth's own.

### Changed
- **Export needs the exported `.mesh`** — a cloth made from bones, and From Mesh joints, are checked against the exported `.mesh`: the one in Mesh File, else the same-name `.mesh` beside the `.gpuc`, else the one other `.mesh` there with the cloth's skeleton (the `.gpuc` then takes its name, and the export says so). Without one, or with one of another skeleton, the export is refused; Force Export exports anyway with the scene's weights, as before. The `.mesh` the last export used is no longer taken for a new export. Keep Structure exported As Imported needs no `.mesh`.
- **Rebuild from Bones** — a part that is no bone grid now says so plainly (for example Onimusha `ch001_20_01`'s cloak, a free-form mesh).
- **Onimusha files that drive mesh vertices** (`ch001_20_01`, `it442_0131_00`, `it442_0135_00`) now import with Keep Structure, and export back byte for byte, instead of being refused. Rebuild from Bones sets Drive Mesh Vertices from the file.

## 0.4.1

### New
- **Diagonal Bend** — a Part export setting, on by default. Off, the quad diagonals carry shear links alone, as some files are made (for example Wilds `ch03_074_0015`). Rebuild from Bones finds it, so such files no longer fall back to Keep Structure.

- **Mesh File** — in the export dialog. From Mesh joint indices now come from the exported `.mesh` itself: the file pasted here, else the same-name `.mesh` beside the `.gpuc`, else the one the last export used. Only its header, skeleton and names are read, so its streaming half is not needed. Needs RE Mesh Editor enabled.

- **Per-link tails** — Rebuild from Bones keeps links whose tail differs from the rest of their shape (for example `ch03_074_0015`'s shear links, stiffer at the hem's front) instead of falling back to Keep Structure. The link shape's header shows how many links have their own tail; setting the shape gives them all its tail again.

### Changed
- **Rebuild from Bones** — particles that sit off their bones in the file keep those offsets as rest offsets, instead of counting as a difference.
- **Keep Structure joints** — where two bones share a place, the `.mesh` beside the `.gpuc` decides which one a driven joint follows. `ch03_100_1005` now exports From Mesh byte for byte.
- **From Mesh joints** — when no `.mesh` is found, or the one found has another skeleton, the export still goes ahead with the scene's weights as before and warns that a mesh and `.gpuc` that do not match may make the cloth behave wrongly in game. Meshes left out of a mesh export (Selected Only) no longer shift the indices when the `.mesh` is found.

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
