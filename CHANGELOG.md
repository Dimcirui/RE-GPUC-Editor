# Change Log

## 1.0.0

**Scenes saved with 0.x are not read: import their .gpuc files again.**

### New
- **Drive Mesh Vertices** — a Header setting: the cloth moves the mesh's vertices near it directly instead of its bones. Experimental for Wilds and RE9.
- **Reference Mesh** — pick the vertices the cloth drives on the exported `.mesh`, imported as it is ("Import Reference Mesh", GPUC: Driven Vertices panel); the picks carry over when it is imported again.
- **RE4 cloth from bones** — a cloth created for RE4 now exports for RE4, driving joints or LOD0's vertices.
- **RE4 collision planes** — Collision From Bones adds a Plane for RE4 cloth; spheres are written as spheres.
- **Force Export** — in the export dialog: exports without a matching `.mesh`, the joints taken from the scene's weights.
- **Edit imported Parts** — move, add, remove and reconnect particles of an imported cloth in Edit Mode, cut slits and repaint its skin; the export writes the file again from the Parts. Works on game files whose parts do not attach to bones. **Import a cloth again to edit it if an earlier version imported it.**
- **Collision planes and spheres for Wilds, RE9 and Onimusha** — a file's own planes and spheres import as objects and export to their own sections, chosen by the Part's Plane and Sphere Bits; Collision From Bones makes planes for every game.
- **Triangle tangent flags** — on an editable imported Part's faces, as attributes you can read and set; new triangles whose UVs span no area get the flag the game's files give them.
- **Attach to Bones** — an imported Part found to be a grid of bone chains follows its bones, and stays its file's Part.
- **Editing links** — in Edit Mode the links between the selected particles (of the shapes ticked in GPUC: Links) can get a stiffness of their own or a preset's, follow the overall stiffness, be removed, or be added between two particles, with undo. On a Part that follows its bones the changes last while its grid stays the same, also when the bones move.
- **Part Groups** — each part block is a Part Group holding its settings, with one or more Parts in it; Parts move between Groups, and empty Groups are kept but not written.
- **Parts of several pieces** — a game file's part made of several pieces of cloth imports as one Part per piece in one Part Group, and each piece attaches to its bones on its own.
- **Stiffness presets** — the four link shapes' stiffness saved and applied to a Part's overall stiffness or to selected links; the built-in ones are listed with them.
- **Named problems** — a failed export, import or tool names its problem and shows what it means and how to fix it; the Part panel shows the fix under a Part's problem. General Tools groups its tools under Parts and Mesh.
- **Anchors** — particles remember where they sit on the mesh; Check and the export say when the mesh moved under them, and Refit to Mesh moves them with it.
- **Editing files that drive mesh vertices** — their Parts can be edited, follow their bones and fit to a mesh; written again from the Parts, the vertices are bound again from the exported `.mesh`. Drive can also be changed on an imported cloth.
- **Non-manifold Parts** — an edge may have more than two faces (a flap on a sheet), and game files whose quads fold over one diagonal (Onimusha's capes) can be edited.
- **Parts with links of their own** — a game file's part that holds links its faces do not give (for example two long soft links in Wilds ch03_066_0015) can now be edited; those links stay between their two particles until one is deleted.
- **Every Part follows its bones** — an imported Part, one made from a mesh or one edited as a mesh moves its particles with its skin bones when they move; Follow Bones does it by hand.
- **Chains of different lengths, side columns, skin by bone segments** — a Part made from bones can take chains of different lengths (a shorter one ends early, the faces past its end left out), Side Columns beside its outer chains, and Skin by Bone Segments as RE9's coats are skinned.
- **Slits and faces left out** — a Part made from bones can be cut down a column or leave faces out (GPUC: Particles > Shape, in Edit Mode); the shape carries over a change of density and comes back when the file is imported again.
- **Attach to Bones on more game files** — the grid is found from the Part's quads whatever its particles' order, with slits and faces left out, so RE9 skirts, dresses and coats and many Wilds files attach; the bones are found by where they are, also in files that skin each bone halfway down it, and the grid may have chains of different lengths or lacking bones anywhere, side columns and its own number of particles between each two bones; the skin style nearer the file's is kept for later densities. The file's skin and quad splits stay while the Part keeps its file's density.
- **Anchor and hem rows** — a Part made from bones can have pinned rows above its top bones and rows past its chains' ends, as the game's files do; they come back when the file is imported again.
- **Project onto Mesh** — an option when creating or regenerating a Part from bones: its particles move onto the mid-surface of the cloth mesh skinned to its bones, and it still regenerates.
- **Fit to Mesh** — moves a cloth's particles onto another mesh, such as the same mesh sculpted or converted for another character, and onto its armature and game; meshes with other faces map by their UVs, and particles without a paired mesh go as the meshes and bones around them moved.
- **Driven bones moved off an imported file's binds** — bound again at their rest on export, instead of an error in Check.
- **Export in a pose** — particles are written where the current pose carries them, as RE Mesh Editor writes the mesh, so a cloth exported with its posed mesh still fits it.
- **Imported cloths in another game** — a Wilds, RE9 or Onimusha file kept as imported exports for the other two.
- **Part from Mesh** — make a Part from any mesh with a UV map and bone weights; it exports and edits like an imported one.
- **Driven Bones** — a Part setting: drive every bone it is skinned to, or only the bones listed.
- **New Parts in imported cloths** — add Parts made from bones to an imported Wilds, RE9 or Onimusha cloth; bones its file does not drive are driven From Mesh.
- **Edit as Mesh** — a Part made from bones becomes an editable mesh, as an imported Part is, and exports as before; Regenerate from Bones goes back.
- **Normalize Skin** — in the Part panel of an editable Part: each particle keeps at most the game's number of bones, summing to 1, as the export writes them. The Part now follows the pose, and Blender's Normalize leaves its value groups alone.
- **Second Joint Hash** — shown in the Collision panel for a file's spheres that hold one. Effect unknown.
- **Unkn Particle 0–5** — six more per-particle values a file holds, kept, painted and exported like the others. Effect unknown.

### Changed
- **One import for a new cloth** — New Cloth replaces Keep Structure and Rebuild from Bones: it keeps the file, and its Attach to Bones option (on by default) attaches each part that is a bone grid. A part that is not stays as in the file instead of sending the whole file back, and driven joints keep the file's binds. Force Rebuild is now Force Attach.
- **Parts are meshes** — every Part keeps its particles and faces as the export writes them; a Part made from bones also keeps its bones and follows them until edited as a mesh. Its faces now show the export's winding and diagonal.
- **Spheres** — written as spheres, chosen by Sphere Bits, instead of as capsules whose ends meet. **Check the Capsule and Sphere Bits of a cloth that has spheres.**
- **Batches** — a cloth of several Parts is split into batches of up to 512 particles between Parts, as the game's files are; it was one batch however large.
- **Export needs the exported `.mesh`** — a cloth made from bones, and From Mesh joints, are checked against it: Mesh File, else the same-name `.mesh` beside the `.gpuc`, else the one other `.mesh` there with the cloth's skeleton, whose name the `.gpuc` then takes. Without one the export is refused unless Force Export is on.
- **RE4 Header, Config and Part values** — named by what they may do; their tooltips say their effect is unknown. A new RE4 Config starts at values that move the cloth.
- **Onimusha files that drive mesh vertices** — now import as they are instead of being refused.
- **Attach to Bones** — optional: a part that is no bone grid is listed in the import's info messages, without a warning popup.
- **Link stiffness** — each link keeps a stiffness of its own or follows its shape's overall one; an imported file's links keep theirs when the overall stiffness changes, until Apply to All. The Part panel shows it as Overall Stiffness Settings.
- **Presets** — GPUC: Presets keeps Header, Config and Part presets; Part presets no longer hold the link stiffness, and Collision, Weld and preview presets are gone.
- **Draw Through** — particles and links can now be drawn through other meshes but not through the cloth's own near side (the default), so a sleeve no longer shows its far side and a bend link shows on the concave side only; Everything and Nothing remain.
- **Preview Simulation** — hidden in Edit Mode unless it is running.

### Fixed
- **Severe slowdown in game** — exported Wilds, RE9 and Onimusha cloth whose triangle count is not a multiple of 4 no longer stalls the GPU. **Export such a cloth again.**
- **Skin of Parts that keep their file's structure** — such a Part now shows its skin as bone groups and follows the pose; painted weights are written into the file.
- **Parts with a quad wound the other way** — they are found as grids and attach to their bones.
- **Undo after bones moved** — undoing a bone move no longer leaves a Part's particles off their bones: the Part's data goes back with its mesh, and a mesh that comes back alone is made again from the data instead of being saved as particle offsets.
- **Finding the exported `.mesh`** — the export also looks for the `.mesh` named after the Mesh Collection, passes over a same-name one of another skeleton, says the folder and names it looked for, and says when the `.mesh` is another game's.
- **Select Part Bones** — no longer fails with an error when the active Part does not follow its bones; it then selects the Part's skin bones.
- **Too many skin bones for RE9** — the export says how many bones skin the cloth and that RE9 files take 256, instead of "skin index out of range".
- **Collision planes and spheres of some game files** — they were read as other data: they now import, and an exported cloth keeps them.

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
