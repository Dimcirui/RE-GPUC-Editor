# RE GPUC Editor

**V1.0.0**

[Change Log](CHANGELOG.md)

This addon allows for creating, editing, importing and exporting RE Engine GPU cloth (.gpuc) files in Blender.

### [Download RE GPUC Editor](https://github.com/Dimcirui/RE-GPUC-Editor/releases/latest)

## Features
 - Generates GPU cloth from bone chains, with extra particles between bones and between chains.
 - Imports existing .gpuc files with their own structure, kept for export; parts that are a grid of bone chains are attached to their bones so they can be regenerated.
 - Particle values can be painted as vertex weights or set exactly on selected particles.
 - Particles can be moved by hand in Edit Mode; the offsets survive rebuilds.
 - Previews an approximate simulation with gravity, wind and draggable colliders.
 - Presets of headers, configs and parts, and stiffness presets of the four link shapes, can be saved and shared.
 - Links can be edited one by one in Edit Mode: their own stiffness, a preset's, removed or added.

## Supported Games
| Game | Support |
|---|---|
| **Monster Hunter Wilds** | Full |
| **Resident Evil Requiem (RE9)** | Full |
| **Onimusha: Way of the Sword** | Full |
| **Resident Evil 4** | New cloth from bones; imported files: values edit only |

## Requirements
* [Blender 4.2 or higher](https://www.blender.org/download/) (5.1 recommended)
* [RE Mesh Editor](https://github.com/NSACloud/RE-Mesh-Editor), to import the mesh and armature the cloth belongs to

## Installation
Download `re_gpuc_editor-<version>.zip` from [Releases](https://github.com/Dimcirui/RE-GPUC-Editor/releases).

In Blender, go to Edit > Preferences > Get Extensions, open the drop-down menu in the top right and click "Install from Disk". Select the zip file; the addon is then usable.

To update, uninstall the old version first. Earlier versions were installed as `ree_gpuc_editor`; uninstall that one too, or both will be loaded. Saved presets are kept.

## Usage Guide
**TL;DR Usage Guide**

1. Import the mesh with RE Mesh Editor.
2. In the RE GPUC sidebar tab, press "Create" under GPUC: General Tools and pick the game, name and armature.
3. Switch to Pose Mode. Select the first bone of each chain and press "Create Part From Parent Bones", or select whole chains and press "Create Part From Selected Bones". Each chain becomes one column of the cloth. Anchor Rows adds pinned rows above the top bones, Anchor Gap apart and skinned as the top row, as the game's files hold the cloth (RE9: one row 5 to 18 mm above the first bones); Hem Rows adds rows past the chains' ends, as far apart as the last rows, so the cloth reaches a hem below the last bones; Side Columns adds columns beside an open part's outer chains the same way. Skin chooses how the particles down a chain are skinned: Bone Heads (a particle on a bone's head is that bone's alone) or Bone Segments (each bone has the particles halfway down it alone and shares the one on its head with the bone above, as RE9's coats are skinned); Attach to Bones takes the one nearer its file's skin. Chains of different lengths are cut to the shortest with "Cut to the shortest chain"; otherwise a shorter chain ends early and the faces past its end are left out. A Part attached from a game file keeps the file's own number of particles between each two bones until Sub Rows or Sub Cols is changed. Project onto Mesh (in the same dialog, and in Regenerate) moves the particles from the lines between the bones onto the mid-surface of the cloth mesh skinned to those bones (between the outside and its lining), as rest offsets: the Part still regenerates, and Reset returns it to the bones. Faces skinned to other bones, such as buckles or the body, are passed over, and particles that find no surface nearby, or land far from their neighbours, take their neighbours' offsets.
4. Set particle values in the Particles panel: "Paint" paints the chosen value as vertex weights, "Set on Selected" sets an exact value. Weights 0 to 1 map to the Part's Mapping Range.
5. Configure the Header, Configs, Parts and colliders in the Object properties tab, or apply presets.
6. (Optional) Preview under GPUC: Preview Simulation > Simulate.
7. Export the mesh with the RE Mesh Editor, then export the cloth with "Export" under GPUC: General Tools. The export checks the cloth against the exported `.mesh`: the one in Mesh File, else beside the `.gpuc` the `.mesh` named as the `.gpuc`, then as the Mesh Collection, then the one other `.mesh` there with the cloth's skeleton; one named otherwise gives the `.gpuc` its name. The export says when the `.mesh` is another game's than the one exported for. Without one it is refused; Force Export exports anyway, the driven joints' indices then taken from the scene's weights. An imported cloth exported As Imported needs no `.mesh`.

After you move bones in Edit Mode, the affected Parts rebuild when you leave it. "Check" reports problems with every Part of the active cloth.

<details>
  <summary>Import modes</summary>

* **New Cloth** (default): a new GPUC collection with the file's particles, faces, links, skinning and values, and the file kept for export. You can edit the values, and the particles and faces themselves (see Editing a Part as a mesh). With no edits, the export is byte for byte the original file. A Wilds, RE9 or Onimusha file can also be exported for the other two (the whole file is then written again from the Parts); an RE4 file only for RE4.
  * **Attach to Bones** (on by default, needs a Mesh Collection) then attaches each part to its bones, as the button of that name does (see Editing a Part as a mesh), so it is regenerated when they move and its density can change. Attaching is optional: a part that no grid of bone chains reproduces stays as in the file, imports, edits and exports as such, and an info message says why; cloths this editor made from bones always attach. **Force Attach** attaches a part even when its grid changes it, its particles, links or skin then no longer the file's, and lists the changes. Onimusha files that drive vertices are instead made again from the bones, their Drive settings taken from the file.
* **Update Existing**: applies the file's values to an existing GPUC collection.

The export writes the particles in the armature's current pose, as RE Mesh Editor writes the mesh (its vertices posed, its bones at rest), so a cloth exported with its mesh in a pose still fits it; the driven joints stay bound at rest. Put the armature in its rest pose (or Rest Position) to export at rest. An imported cloth in a pose is written again from its Parts; one that cannot be, and RE4, keeps its file's rest positions, and the export says so.

Any Wilds, RE9 or Onimusha cloth can be converted between those three games by choosing the game in the export dialog. A particle skinned to more bones than the game takes (RE9 8, Wilds and Onimusha 6) keeps the heaviest, and the export says how many.

</details>

<details>
  <summary>Driving mesh vertices (experimental)</summary>

By default the cloth moves the bones its Parts are made from. With Drive set to Mesh Vertices in the Header, it moves the mesh's vertices near it directly, in every LOD (RE4: LOD0), as some Onimusha and RE4 files do. It is experimental for Wilds and RE9.

The vertices are those of the exported `.mesh`, so export the mesh first, and the `.gpuc` again after every mesh export. Which of them the cloth drives is an export setting (Header > Export Settings, Vertex Selection); the file keeps only the result:

* **By Distance**: the `.mesh`'s vertices within Drive Distance of the cloth's rest surface. Quick, but it takes everything that close, such as a body layer under the cloth. The `.mesh` comes from Mesh File, else the same-name `.mesh` beside the `.gpuc`.
* **Reference Mesh**: the vertices you pick. "Import Reference Mesh" imports the exported `.mesh` as it is (every LOD, groups not merged) and checks every vertex is the file's, in its order. In its Edit Mode, the GPUC: Driven Vertices panel adds the selected vertices to its `GPUC_Drive` vertex group, removes them or selects the driven ones; "Select Near the Cloth" selects by distance as a start, and "LOD0 to Later LODs" gives the later LODs LOD0's choice. The export checks the cloth against that `.mesh` (Mesh File must be blank or the same) and refuses when the reference no longer matches it. After exporting the mesh again, import the reference again: the picked vertices carry over (vertex for vertex where a submesh is unchanged, else by place) and the export says how many found no place.

Triangles per Vertex (Export Settings) sets how many nearby cloth triangles each driven vertex follows, the nearest weighted most.

Onimusha files that drive vertices import with Drive set to Mesh Vertices and its values from the file, and their Parts can be edited like any other's. An untouched export keeps the file's bindings; once the file is written again from its Parts (an edited Part, a new one, a change of Drive), the vertices are bound again from the exported .mesh, so export the mesh first or give its path in Mesh File. With Attach to Bones they are made again from the bones, Drive and its values set from the file.

</details>

<details>
  <summary>Moving particles</summary>

Select a generated Part and press Tab to enter Edit Mode. Move particles with `G`, axis constraints or proportional editing; valid positions are saved when you leave Edit Mode.

* Offsets are stored in the armature's rest space. They are kept when the cloth rebuilds after bone heads move, and interpolated over the grid when the density changes.
* Vertex count, connectivity and UVs are protected: deleting or merging vertices or changing UVs is undone when you leave Edit Mode. To change them, use Edit as Mesh (see Editing a Part as a mesh).
* "Reset Selected" / "Reset All" return particles to the positions generated from the bones.
* A file exported with offsets attaches to its bones again, its offsets kept.
* Imported Parts: see below.

</details>

<details>
  <summary>Editing a Part as a mesh</summary>

A Part imported from a file (not attached to bones), made from bones and turned with Edit as Mesh, or made from any mesh with Part from Mesh (General panel), is a mesh of quads and triangles: each vertex is a particle, and its links come from the faces, as the game's own cloth is made. In Edit Mode you can move particles, subdivide, cut loops, extrude, delete or fill faces, and cut slits. Its skin is in one vertex group per bone, which you can paint. The changes are taken when you go back to Object Mode.

* Faces must have 3 or 4 corners, every particle must be on a face, and every particle needs a skin weight; otherwise the edit is undone, with the reason in the Part panel.
* New particles take their values and skin from their vertex groups, which Blender fills in as you edit. New links take their shape's overall stiffness.
* The Part deforms by its armature, so it follows the pose and Blender's Normalize and Auto Normalize change only the bone groups, never the value groups. "Normalize Skin" in the Part panel gives each particle at most the game's number of bones (6, RE9 8), summing to 1, as the export writes them; locked groups keep their weights, and in Weight Paint with a selection mask only the selected particles change.
* Once a Part is edited, the export writes the whole file again from the Parts instead of patching the original. The driven joints stay where the file binds them, or at their bones' rest when the bones moved (also without an edit); a joint whose particles were removed, or whose bone now skins new particles, is driven by the particles skinned to its bone most.
* Each face holds its triangles' tangent flags as the attributes `gpuc_tangent_flags_a` (its first triangle) and `gpuc_tangent_flags_b` (a quad's second). Read them in the Spreadsheet and set them with Mesh > Set Attribute in Edit Mode, in an edit that changes no faces. Bits 0–2 flip the tangent at a corner and bit 3 takes no tangent from the UVs; they change how driven joints turn about the cloth's normal, not how the cloth moves. New triangles get bit 3 where their UVs span no area, else 0.
* The file's Parts cannot be removed, and Update Existing is refused, once a Part is edited.
* An imported cloth (not RE4) also takes new Parts made from bones, as is or edited as a mesh. The export then writes the whole file again, the file's particles first. A new Part on bones the file already drives exports As Imported; one on other bones needs Joint Indices From Mesh, and those bones are driven as in a cloth made from bones.
* Not editable: imported RE4 cloths. A file that holds interpolations and restrictions (Onimusha ch001_00_01) keeps its particles and faces, since writing it again from its Parts would lose them; its skin weights can be painted, and the export writes them into the file, adding any new skin bone to the file's list. A part whose links differ from the ones its faces give (links of its own, or ones it leaves out) is editable: those links stay as they are between their two particles until one is deleted. Its triangles pair into quads by their links, also where two quads fold over one diagonal (Onimusha's capes). An edge may have more than two faces, such as a flap on a sheet; the import says how many links differ.
* Anchors: a cloth imported with a Mesh Collection remembers where each particle sits on the mesh (Fit to Mesh, Project onto Mesh, Part from Mesh and Anchor to Mesh remember it too). When the mesh later moves under the particles (sculpted, imported again with the same faces), Check and the export say how many and how far, and Refit to Mesh (General Tools) moves each particle by its mesh's move, an edit made to the particle kept. A particle edited by hand does not count as the mesh moving. When the mesh has other faces, Check says so; Anchor to Mesh then remembers the places anew, or Fit to Mesh moves the particles from a copy of the old mesh.
* Fit to Mesh (General Tools) moves the active cloth's particles from the mesh they sit on to another mesh: the same mesh sculpted, or converted for another character or game. Meshes pair by material name. Each particle keeps its place on the mesh and its distance from it, so the cloth fits the new shape as it fitted the old: on the same triangles when the two meshes have the same faces (RE Mesh Editor keeps them when it exports again), else at the same UV (both need a UV map). A particle whose mesh finds no pair goes as the paired meshes and the bones around it moved (a displacement field through them); particles more than 10 cm from the mesh go with their skin bones when the cloth changes armature, and otherwise stay where they are. The notice says how many went each way. Use Its Armature also puts the cloth on the target's armature (its Parts' skin, header and collisions), its bones found by name. Game sets the header's game to the target mesh's; the header's values stay as they are. A Part made from bones keeps following its bones. An imported cloth's driven joints whose bones moved off where its file binds them (over 1 mm) are bound again at their bones' rest on export, and the export says how many.
* Part from Mesh makes a Part of the active cloth from the active mesh, which is left as it is: its vertices become the particles at their rest place (its Armature modifiers are left out, its other modifiers applied), its faces give the links, its UV map the tangents, its weights to the armature's bones the skin (the heaviest up to the game's number of bones). Give it a UV map and a bone weight on every vertex first, for example with a Data Transfer modifier from the body mesh. Its Driven Bones start as an empty list: list the bones the cloth moves, and paint Simulation weight 0 where it is held.
* Driven Bones (Part panel) chooses which bones the Part's particles drive: Skin Bones, every bone it is skinned to (a Part made from bones: its own bones), or only the Listed ones. List the bones selected on the armature with +, or every skin bone at once and remove the ones the cloth must not move, such as body bones in weights taken from the body. A listed bone that skins no particle is not driven, and the export says so.
* A Part that does not follow a bone grid (imported, made from a mesh, or edited as a mesh) still follows its bones by its skin: when its skin bones' rest changes (the armature leaves Edit Mode), its particles move as the armature carries them. Check says when bones moved and the particles did not follow (for example in a file opened again), and Follow Bones (Part panel) moves them. Parts whose particles keep their file's places (imported RE4 cloths) cannot follow, and Check says so.
* Attach to Bones (Part panel of an editable imported Part, and the import option of that name) makes it follow its bones: found as a grid of bone chains, it is regenerated when they move and its density can change, while it stays its file's Part (its driven joints keep the file's binds). The grid is found from the Part's quads, whatever order its particles are stored in, with its slits and the faces it leaves out, and its bones are found by the particles on their heads, so many game files attach (for example RE9 skirts, dresses and coats, and many Wilds files). While the Part keeps its file's density it also keeps the file's skin and the way the file splits each quad. It is refused, the Part left as it is, when no such grid makes the same particles and links. Once attached, the export writes the whole file again.
* Shape (GPUC: Particles panel, a Part that follows its bones, in Edit Mode): Cut Slit cuts the grid down the selected particles' column, from below the top one selected to the bottom, so each side moves on its own; the particles there are doubled, and a bend link holds the slit's top, as the game's files have. Remove Faces leaves the selected faces out, for a shape that is not a rectangle; particles no face uses go. Restore Shape closes every slit and brings every face back. The shape carries over a change of density.
* Edit as Mesh writes a Part made from bones as its export would: its faces, skin, links and tails, so it exports as before. It no longer follows its bones; its welds keep their pairs. Regenerate from Bones (Part panel) lays it out from its bones again and loses the edits.

</details>

<details>
  <summary>Link stiffness and editing links</summary>

Each link has a stiffness of its own, or takes its shape's overall stiffness (Vertical, Ring, Shear, Bend). The links of an imported file keep the file's stiffness as their own, so changing the overall stiffness leaves the file's values as they are.

* Overall Stiffness Settings (Part panel): each shape's overall stiffness, and how many of its links have their own. Apply to All gives every link of the shape the overall stiffness. A stiffness preset applies to the overall stiffness (Apply), or the Part's overall stiffness is saved as one (+).
* GPUC: Links (sidebar, a Part in Edit Mode): the links between the selected particles are the ones the tools change, of the shapes ticked there. They are drawn thicker; Selected Links Draw Through Objects (Display > Links) draws them in front of every mesh.
  * Set Stiffness gives them a stiffness of their own; Follow Overall makes them take the overall stiffness again; a stiffness preset gives each its shape's stiffness of the preset; + saves a preset from the first selected link of each shape.
  * Remove Links removes them; Add Link Between Two adds a link of a shape between two selected particles. The Part is then written again from the Parts on export. On a Part that follows its bone grid, links removed or added and the links' own stiffness last while its grid stays the same, also when its bones move; changing its density, shape, Other Diagonal or Diagonal Bend gives the new grid's links again, and the Part panel says so.
  * Undo and redo in Edit Mode take the link edits back and forth. After particles were added or removed, leave Edit Mode once before editing links.
* Stiffness presets hold all four shapes and are shared by every game; the built-in ones come from game files.

</details>

<details>
  <summary>Presets</summary>

The GPUC: Presets panel saves Header, Config and Part values as JSON presets, and applies them to selected objects of the same kind (with undo).

* Presets are saved per game and apply only to objects of that game.
* A Part preset holds the physics values, not the link stiffness (see stiffness presets) or per-particle values.
* Object references, bone bindings, root joints, particle offsets, skinning and topology are not saved.
* "Open Preset Folder" opens the folder, so the JSON files can be shared directly.

</details>

<details>
  <summary>RE4</summary>

**New cloth.** Create a cloth with the game RE4 and make Parts from bones as for the other games. The export writes a whole RE4 file: the particles are skinned by the exported `.mesh`'s weighted bones, so a matching `.mesh` is needed (or Force Export, which takes the scene's weights). Drive Mesh Vertices drives LOD0's vertices, as RE4's own files do.

* Each Part's particle values (Simulation weight, Collision radius, Inverse mass, Max distance, Backstop radius), link settings, friction, bounce, stickiness and collision masks, the Configs' RE4 values and the Header's Max Velocity are written.
* The Config values, the Header's Collision Iterations and Solver Iterations and the Part's Long Range Attachment Stretchiness are named by what they may do; their effects are unknown. Simulation Strength or Blend Weight at 0 leaves the mesh unmoved. Fields RE4 does not have (CLSP flags, curves, OBB) are hidden.
* Capsules, spheres and planes are written as colliders, each joint by its bone's name. A plane (Collision From Bones with Plane) is endless, through its joint facing its Normal (+Y by default). The simulation cannot be previewed.

**Imported files** need no Mesh Collection; their Parts are not attached to bones.

* You can edit the same values as above. Names, the number of Parts and Configs, bone bindings and colliders cannot be changed.
* Export with RE4 selected and Joint Indices set to As Imported.

</details>

### When something fails

A failed export, import or tool names its problem in the report, for example `[NoMatchingMesh]`, and shows a window that says what it means and how to fix it. A Part's problem in Check is shown in the Part panel with the first step of its fix.

## License and use

RE GPUC Editor is released under the [GPL-3.0-or-later](LICENSE). `data/skeletons.json` is generated from Monster Hunter Wilds `.fbxskel` files and holds only bone names, name hashes, parent links and rest poses, used to place colliders on body bones.

RE GPUC Editor is meant for modding and research in single-player games. It does not support cheating, interfering with anti-cheat or online services, or distributing copyrighted game files. You are responsible for following the law and each game's terms. None of this changes the rights the GPL grants.

RE GPUC Editor is an independent project and is not affiliated with or endorsed by Capcom. All trademarks are the property of their respective owners.

## Credits
- [NSACloud](https://github.com/NSACloud) - RE Mesh Editor and RE Chain Editor, whose layout and naming this addon follows
- [lxchhh3](https://github.com/lxchhh3) - GPUC format research and the dynamics model behind the preview simulation
- [XenonBaruku](https://github.com/XenonBaruku) - [MHWs-Research-Templates](https://github.com/XenonBaruku/MHWs-Research-Templates), the original GPUC reference
- [ShiyumeMeguri](https://github.com/ShiyumeMeguri) - [RuriClothPhysics](https://github.com/ShiyumeMeguri/RuriClothPhysics), the approach behind the preview simulation and the reference for its forces and colliders
