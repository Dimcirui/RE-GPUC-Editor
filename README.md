# RE GPUC Editor

**V1.0.0**

[Change Log](CHANGELOG.md)

This addon allows for creating, editing, importing and exporting RE Engine GPU cloth (.gpuc) files in Blender.

### [Download RE GPUC Editor](https://github.com/Dimcirui/RE-GPUC-Editor/releases/latest)

## Features
 - Generates GPU cloth from bone chains, with extra particles between bones and between chains.
 - Imports existing .gpuc files: rebuilt from bones so they can be regenerated, or kept with their original structure for value edits.
 - Particle values can be painted as vertex weights or set exactly on selected particles.
 - Particles can be moved by hand in Edit Mode; the offsets survive rebuilds.
 - Previews an approximate simulation with gravity, wind and draggable colliders.
 - Presets of headers, configs, parts, collisions and welds can be saved and shared.

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
3. Switch to Pose Mode. Select the first bone of each chain and press "Create Part From Parent Bones", or select whole chains and press "Create Part From Selected Bones". Each chain becomes one column of the cloth.
4. Set particle values in the Particles panel: "Paint" paints the chosen value as vertex weights, "Set on Selected" sets an exact value. Weights 0 to 1 map to the Part's Mapping Range.
5. Configure the Header, Configs, Parts and colliders in the Object properties tab, or apply presets.
6. (Optional) Preview under GPUC: Preview Simulation > Simulate.
7. Export the mesh with the RE Mesh Editor, then export the cloth with "Export" under GPUC: General Tools. The export checks the cloth against the exported `.mesh`: the one in Mesh File, else the same-name `.mesh` beside the `.gpuc`, else the one other `.mesh` there with the cloth's skeleton, whose name the `.gpuc` then takes. Without one it is refused; Force Export exports anyway, the driven joints' indices then taken from the scene's weights. A Keep Structure cloth exported As Imported needs no `.mesh`.

After you move bones in Edit Mode, the affected Parts rebuild when you leave it. "Check" reports problems with every Part of the active cloth.

<details>
  <summary>Import modes</summary>

* **Rebuild from Bones** (default): rebuilds the cloth with the editor's grid model, so it can be regenerated. Needs a Mesh Collection. Files whose structure does not fit the model are imported with Keep Structure instead, with a message saying why.
* **Keep Structure**: keeps the file's particles, links and skinning, and lets you edit the values, and the particles and faces themselves (see Editing a Part as a mesh). With no edits, the export is byte for byte the original file. A Wilds, RE9 or Onimusha file can also be exported for the other two (the whole file is then written again from the Parts); an RE4 file only for RE4.
* **Update Existing**: applies the file's values to an existing GPUC collection.

Any Wilds, RE9 or Onimusha cloth can be converted between those three games by choosing the game in the export dialog. A particle skinned to more bones than the game takes (RE9 8, Wilds and Onimusha 6) keeps the heaviest, and the export says how many.

</details>

<details>
  <summary>Driving mesh vertices (experimental)</summary>

By default the cloth moves the bones its Parts are made from. With Drive set to Mesh Vertices in the Header, it moves the mesh's vertices near it directly, in every LOD (RE4: LOD0), as some Onimusha and RE4 files do. It is experimental for Wilds and RE9.

The vertices are those of the exported `.mesh`, so export the mesh first, and the `.gpuc` again after every mesh export. Which of them the cloth drives is an export setting (Header > Export Settings, Vertex Selection); the file keeps only the result:

* **By Distance**: the `.mesh`'s vertices within Drive Distance of the cloth's rest surface. Quick, but it takes everything that close, such as a body layer under the cloth. The `.mesh` comes from Mesh File, else the same-name `.mesh` beside the `.gpuc`.
* **Reference Mesh**: the vertices you pick. "Import Reference Mesh" imports the exported `.mesh` as it is (every LOD, groups not merged) and checks every vertex is the file's, in its order. In its Edit Mode, the GPUC: Driven Vertices panel adds the selected vertices to its `GPUC_Drive` vertex group, removes them or selects the driven ones; "Select Near the Cloth" selects by distance as a start, and "LOD0 to Later LODs" gives the later LODs LOD0's choice. The export checks the cloth against that `.mesh` (Mesh File must be blank or the same) and refuses when the reference no longer matches it. After exporting the mesh again, import the reference again: the picked vertices carry over (vertex for vertex where a submesh is unchanged, else by place) and the export says how many found no place.

Triangles per Vertex (Export Settings) sets how many nearby cloth triangles each driven vertex follows, the nearest weighted most.

Onimusha files that drive vertices import with Keep Structure. Rebuild from Bones sets Drive and its values from the file.

</details>

<details>
  <summary>Moving particles</summary>

Select a generated Part and press Tab to enter Edit Mode. Move particles with `G`, axis constraints or proportional editing; valid positions are saved when you leave Edit Mode.

* Offsets are stored in the armature's rest space. They are kept when the cloth rebuilds after bone heads move, and interpolated over the grid when the density changes.
* Vertex count, connectivity and UVs are protected: deleting or merging vertices or changing UVs is undone when you leave Edit Mode. To change them, use Edit as Mesh (see Editing a Part as a mesh).
* "Reset Selected" / "Reset All" return particles to the positions generated from the bones.
* A file exported with offsets may import with Keep Structure. Keep the .blend file to go on editing the offsets.
* Keep Structure Parts: see below.

</details>

<details>
  <summary>Editing a Part as a mesh</summary>

A Part imported with Keep Structure, made from bones and turned with Edit as Mesh, or made from any mesh with Part from Mesh (General panel), is a mesh of quads and triangles: each vertex is a particle, and its links come from the faces, as the game's own cloth is made. In Edit Mode you can move particles, subdivide, cut loops, extrude, delete or fill faces, and cut slits. Its skin is in one vertex group per bone, which you can paint. The changes are taken when you go back to Object Mode.

* Faces must have 3 or 4 corners, every particle must be on a face, and every particle needs a skin weight; otherwise the edit is undone, with the reason in the Part panel.
* New particles take their values and skin from their vertex groups, which Blender fills in as you edit. New links take the commonest tail of their shape on the Part.
* The Part deforms by its armature, so it follows the pose and Blender's Normalize and Auto Normalize change only the bone groups, never the value groups. "Normalize Skin" in the Part panel gives each particle at most the game's number of bones (6, RE9 8), summing to 1, as the export writes them; locked groups keep their weights, and in Weight Paint with a selection mask only the selected particles change.
* Once a Part is edited, the export writes the whole file again from the Parts instead of patching the original. The driven joints stay where the file binds them; a joint whose particles were removed, or whose bone now skins new particles, is driven by the particles skinned to its bone most.
* Each face holds its triangles' tangent flags as the attributes `gpuc_tangent_flags_a` (its first triangle) and `gpuc_tangent_flags_b` (a quad's second). Read them in the Spreadsheet and set them with Mesh > Set Attribute in Edit Mode, in an edit that changes no faces. Bits 0–2 flip the tangent at a corner and bit 3 takes no tangent from the UVs; they change how driven joints turn about the cloth's normal, not how the cloth moves. New triangles get bit 3 where their UVs span no area, else 0.
* The file's Parts cannot be removed, and Update Existing is refused, once a Part is edited.
* A Keep Structure cloth (not RE4) also takes new Parts made from bones, as is or edited as a mesh. The export then writes the whole file again, the file's particles first. A new Part on bones the file already drives exports As Imported; one on other bones needs Joint Indices From Mesh, and those bones are driven as in a cloth made from bones.
* Not editable: RE4 Keep Structure cloth, files that drive mesh vertices, and the few parts whose links are not the ones their faces give (the import says which).
* Part from Mesh makes a Part of the active cloth from the active mesh, which is left as it is: its vertices become the particles at their rest place (its Armature modifiers are left out, its other modifiers applied), its faces give the links, its UV map the tangents, its weights to the armature's bones the skin (the heaviest up to the game's number of bones). Give it a UV map and a bone weight on every vertex first, for example with a Data Transfer modifier from the body mesh. Its Driven Bones start as an empty list: list the bones the cloth moves, and paint Simulation weight 0 where it is held.
* Driven Bones (Part panel) chooses which bones the Part's particles drive: Skin Bones, every bone it is skinned to (a Part made from bones: its own bones), or only the Listed ones. List the bones selected on the armature with +, or every skin bone at once and remove the ones the cloth must not move, such as body bones in weights taken from the body. A listed bone that skins no particle is not driven, and the export says so.
* Edit as Mesh writes a Part made from bones as its export would: its faces, skin, links and tails, so it exports as before. It no longer follows its bones; its welds keep their pairs. Regenerate from Bones (Part panel) lays it out from its bones again and loses the edits.

</details>

<details>
  <summary>Presets</summary>

The GPUC: Presets panel saves Header, Config, Part, Collision, Weld and preview force and collider values as JSON presets, and applies them to selected objects of the same kind (with undo).

* Presets are saved per game and apply only to objects of that game. Preview forces and colliders are shared by all games.
* A Part preset holds the physics values and the four link shapes' settings, not per-particle values.
* Object references, bone bindings, root joints, particle offsets, skinning and topology are not saved.
* "Open Preset Folder" opens the folder, so the JSON files can be shared directly.

</details>

<details>
  <summary>RE4</summary>

**New cloth.** Create a cloth with the game RE4 and make Parts from bones as for the other games. The export writes a whole RE4 file: the particles are skinned by the exported `.mesh`'s weighted bones, so a matching `.mesh` is needed (or Force Export, which takes the scene's weights). Drive Mesh Vertices drives LOD0's vertices, as RE4's own files do.

* Each Part's particle values (Simulation weight, Collision radius, Inverse mass, Max distance, Backstop radius), link settings, friction, bounce, stickiness and collision masks, the Configs' RE4 values and the Header's Max Velocity are written.
* The Config values, the Header's Collision Iterations and Solver Iterations and the Part's Long Range Attachment Stretchiness are named by what they may do; their effects are unknown. Simulation Strength or Blend Weight at 0 leaves the mesh unmoved. Fields RE4 does not have (CLSP flags, curves, OBB) are hidden.
* Capsules, spheres and planes are written as colliders, each joint by its bone's name. A plane (Collision From Bones with Plane) is endless, through its joint facing its Normal (+Y by default). The simulation cannot be previewed.

**Imported files** use Keep Structure automatically and need no Mesh Collection.

* You can edit the same values as above. Names, the number of Parts and Configs, bone bindings and colliders cannot be changed.
* Export with RE4 selected and Joint Indices set to As Imported.
* Rebuild from Bones does not take RE4 files yet.

</details>

## License and use

RE GPUC Editor is released under the [GPL-3.0-or-later](LICENSE). `data/skeletons.json` is generated from Monster Hunter Wilds `.fbxskel` files and holds only bone names, name hashes, parent links and rest poses, used to place colliders on body bones.

RE GPUC Editor is meant for modding and research in single-player games. It does not support cheating, interfering with anti-cheat or online services, or distributing copyrighted game files. You are responsible for following the law and each game's terms. None of this changes the rights the GPL grants.

RE GPUC Editor is an independent project and is not affiliated with or endorsed by Capcom. All trademarks are the property of their respective owners.

## Credits
- [NSACloud](https://github.com/NSACloud) - RE Mesh Editor and RE Chain Editor, whose layout and naming this addon follows
- [lxchhh3](https://github.com/lxchhh3) - GPUC format research and the dynamics model behind the preview simulation
- [XenonBaruku](https://github.com/XenonBaruku) - [MHWs-Research-Templates](https://github.com/XenonBaruku/MHWs-Research-Templates), the original GPUC reference
- [ShiyumeMeguri](https://github.com/ShiyumeMeguri) - [RuriClothPhysics](https://github.com/ShiyumeMeguri/RuriClothPhysics), the approach behind the preview simulation and the reference for its forces and colliders
