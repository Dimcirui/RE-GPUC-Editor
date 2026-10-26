# RE GPUC Editor

**V0.4.0**

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
* **Keep Structure**: keeps the file's particles, links and skinning, and lets you edit the values. With no edits, the export is byte for byte the original file. It can only be exported for the game the file came from.
* **Update Existing**: applies the file's values to an existing GPUC collection.

A cloth generated from bones or imported with Rebuild from Bones can be converted between Wilds, RE9 and Onimusha by changing the Game in its Header.

</details>

<details>
  <summary>Driving mesh vertices (experimental)</summary>

By default the cloth moves the bones its Parts are made from. With Drive set to Mesh Vertices in the Header, it moves the mesh's vertices near it directly, in every LOD, as some Onimusha files do. It is experimental for Wilds and RE9, where no game file does this.

* The export reads the vertices from the exported `.mesh` (Mesh File, else the same-name `.mesh` beside the `.gpuc`), so export the mesh first, and the `.gpuc` again after every mesh export. Without the `.mesh` the export is refused.
* Vertices closer than Drive Distance to the cloth's rest surface are driven. Vertex groups on the mesh force vertices in (`GPUC_Drive`) or out (`GPUC_NoDrive`); only the LODs imported into the scene can use them.
* Each driven vertex follows up to Triangles per Vertex nearby cloth triangles, the nearest weighted most.
* Onimusha files that drive vertices import with Keep Structure. Rebuild from Bones sets Drive and its values from the file.

</details>

<details>
  <summary>Moving particles</summary>

Select a generated Part and press Tab to enter Edit Mode. Move particles with `G`, axis constraints or proportional editing; valid positions are saved when you leave Edit Mode.

* Offsets are stored in the armature's rest space. They are kept when the cloth rebuilds after bone heads move, and interpolated over the grid when the density changes.
* Vertex count, connectivity and UVs are protected: deleting or merging vertices or changing UVs is undone when you leave Edit Mode.
* "Reset Selected" / "Reset All" return particles to the positions generated from the bones.
* A file exported with offsets may import with Keep Structure. Keep the .blend file to go on editing the offsets.
* Particles of Keep Structure Parts (including RE4) cannot be moved.

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
* The Config values, the Header's Collision Iterations (`+0x44`) and Solver Iterations (`+0x60`) and the Part's Long Range Attachment Stretchiness (`+0x30`) are named by guess: each tooltip gives the offset and says Unconfirmed. A new Config starts at the game files' commonest values; Simulation Strength or Blend Weight at 0 leaves the mesh unmoved in game. Things RE4 has no field for (CLSP flags, curves, OBB) are hidden.
* Capsules, spheres and planes are written as colliders, each joint by its bone's name. A plane (Collision From Bones with Plane) is endless, through its joint facing the Normal in the joint's space (+Y, as the game files' ground); the sign of its Distance in the game is a guess. RE4 has no CLSP: CLSP Flags A / B stay -1. The simulation cannot be previewed.

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
