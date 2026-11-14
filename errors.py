"""Named problems, as RE Mesh Editor names its export errors: each with what it means and how to fix it. A message
is named by the first entry whose pattern it matches (``explain``); the message itself still says the particulars
(which Part, which bone, which file). ``show`` puts one in a popup beside the report."""
import re
import textwrap

import bpy

# (name, title, patterns, what it means, how to fix it)
TABLE = [
    ("NoMatchingMesh", "No matching .mesh",
     [r"no \.mesh found", r"no matching mesh", r"does not match this cloth", r"have the cloth's skeleton",
      r"no other \.mesh there"],
     "The export takes the driven joints' indices from the exported .mesh, and finds none whose skeleton is the "
     "cloth armature's.",
     "Export the mesh with RE Mesh Editor beside where the .gpuc goes (the same name, or the Mesh Collection's), or "
     "give its path in Mesh File. Turn on Force Export to export with the indices taken from the scene's weights."),
    ("MeshNotReadable", "The .mesh cannot be read",
     [r"RE Mesh Editor is not enabled", r"cannot be read", r"could not import", r"has no skeleton",
      r"weighted-bone table is invalid", r"belong to no submesh"],
     "Reading a .mesh needs RE Mesh Editor, and the file must be one it reads.",
     "Enable RE Mesh Editor in the add-on preferences, and check that the file is the exported .mesh of this "
     "character."),
    ("ClothOnAnotherArmature", "The cloth is on another armature",
     [r"the cloth is on .*, not on"],
     "The Mesh Collection picked has another armature than the cloth, so its joint indices would not fit.",
     "Pick the cloth's own mesh collection, or move the cloth onto the picked one with Fit to Mesh (Use Its "
     "Armature)."),
    ("VertexDriveNeedsMesh", "Driving mesh vertices needs the .mesh",
     [r"Drive Mesh Vertices needs the exported \.mesh"],
     "A cloth that drives mesh vertices binds them from the exported .mesh, in its vertex order.",
     "Export the mesh with RE Mesh Editor first, then put its path in Mesh File or export the .gpuc beside it. "
     "Export the .gpuc again after every export of the mesh."),
    ("NoDrivenVertices", "No mesh vertex to drive",
     [r"lies within .* of the cloth", r"holds no vertex", r"no LOD0 vertex is driven", r"has no vertex in"],
     "No vertex of the mesh is near enough to the cloth, or none is picked on the reference mesh.",
     "Raise Drive Distance in the Header, or pick the driven vertices on the reference mesh in Edit Mode."),
    ("ReferenceMeshMismatch", "The reference mesh does not match",
     [r"reference mesh", r"no longer matches"],
     "The vertices picked on the reference mesh no longer stand for the exported .mesh's.",
     "Import the reference mesh again from the exported .mesh (GPUC: Driven Vertices), pick the vertices again, "
     "and do not edit its vertices."),
    ("NothingDriven", "No bone is driven",
     [r"no bone is driven"],
     "No Part drives a bone that has weights in the mesh, so nothing in game would move.",
     "List the bones a Part drives (Driven Bones in the Part panel), or give those bones weights in the mesh."),
    ("NewBonesNeedMeshIndices", "New Parts drive bones the file does not",
     [r"drive bones its file does not"],
     "An imported cloth takes its driven joints' indices from its file; new bones need the exported .mesh's.",
     "Export with Joint Indices: From Mesh, the .mesh exported beside it."),
    ("TooManySkinBones", "Too many skin bones",
     [r"bones skin the cloth's particles, and .* take", r"more than 8 skin bones"],
     "The game indexes only so many skin bones in one cloth (RE9 256) or one particle (RE4 8).",
     "Skin the Parts to fewer bones: Normalize Skin in the Part panel keeps the heaviest, or split the cloth."),
    ("SkinBoneMissing", "A skin bone is missing",
     [r"skin bone .* is not in the armature", r"skin bones are gone", r"skinning names bone",
      r"skin names bones the armature lacks", r"bones are gone"],
     "A Part is skinned to a bone the armature does not have (renamed, deleted, or another character's mesh).",
     "Pick the mesh collection the cloth belongs to, rename the bone back, or paint the particles to other bones."),
    ("PartInEditMode", "A Part is in Edit Mode",
     [r" is in .* mode$", r"Return to Object Mode"],
     "Its edits are taken when it leaves Edit Mode.",
     "Go back to Object Mode, then try again."),
    ("PartOffItsBones", "A Part no longer fits its bones",
     [r"\(regenerate it\)", r"moved off the rest heads"],
     "The bones a Part was made from moved since it was laid out.",
     "Regenerate the Part (Regenerate Particles From Part), or move the bones back."),
    ("ClothIncomplete", "The cloth is incomplete",
     [r"the cloth has no header", r"the cloth has no Parts", r"has no Config of this cloth",
      r"has no armature", r" has no particles$"],
     "A cloth needs its Header on its armature, a Config, and Parts with particles under it.",
     "Create the cloth with Create in General Tools and make its Parts from bones or a mesh, or import a .gpuc."),
    ("OtherGame", "Another game's cloth",
     [r"keeps a .* file's structure: export it for", r"cloth cannot drive mesh vertices", r"onto a .* cloth"],
     "The cloth keeps a file of another game, or that game cannot do what it is set to.",
     "Export for the game it came from, set Drive to Joints, or import the file as a new cloth."),
    ("FileStructureKept", "The file's structure must stay",
     [r"keeps its file's structure as it is", r"keeps a file's structure in a cloth", r"of its file's .* Parts",
      r"are not its file's", r"were edited: import the file", r"not the same structure",
      r"import it again", r"import the original file again", r"import again"],
     "An imported cloth that cannot be written again from its Parts exports by patching its file, and the file's "
     "Parts and particles must be the ones it holds.",
     "Import the file again as a new cloth, or undo the change."),
    ("RE4Limits", "RE4 keeps its file",
     [r"^RE4", r": RE4 ", r"RE4 keeps", r"RE4 cannot", r"RE4 has no"],
     "An imported RE4 cloth keeps its file's skinning, outputs, collisions and structure; only its values change.",
     "Edit its values only, or make a new RE4 cloth from bones."),
    ("BadGeometry", "Degenerate particles",
     [r"degenerate rest triangle", r"cannot construct rest", r"non-finite", r"merge or move those particles"],
     "Some triangles have no area, or particles sit on one another, so their normals cannot be made.",
     "Move or merge the particles named (Edit Mode, Merge by Distance), or undo the edit."),
    ("PickMeshCollection", "Pick the mesh collection",
     [r"pick the mesh collection", r"pick the mesh this", r"needs its weighted bones", r"no mesh collection",
      r"import it with the mesh collection"],
     "The cloth is checked against the character's armature and weights, which come with its RE Mesh Editor "
     "collection.",
     "Import the character's .mesh with RE Mesh Editor, and pick its collection in Mesh Collection."),
    ("NotReadableGpuc", "The .gpuc cannot be taken",
     [r"Kernel Flags lack", r"particles are not all together", r"can be imported"],
     "The file is no cloth the editor reads: another format, or a layout it does not know.",
     "Check that it is a .gpuc of a supported game (Wilds, RE9, Onimusha, RE4)."),
    ("NotACloth", "The mesh is no cloth",
     [r"more than 4 corners", r"has no UV map", r"particles are on no face", r"two faces share all",
      r"has no skin weights", r"no weight to a bone", r"has no faces", r"edges have more than"],
     "A Part's mesh needs faces of 3 or 4 corners, a UV map, every vertex on a face and weighted to a bone.",
     "Fix the mesh as the message says (split n-gons, unwrap it, delete loose vertices, give the vertices weights,"
     " for example with a Data Transfer modifier from the body mesh), then try again."),
    ("NotABoneGrid", "No grid of bone chains",
     [r"lie on no lattice", r"lie on no rows-by-columns grid", r"bones down a column", r"a bone sits on two",
      r"its bone grid would change it", r"links are not the ones"],
     "Attach to Bones needs the Part to be a grid whose particles sit on its bones; this one is not.",
     "Keep it as it is (it still edits, follows its bones and fits to a mesh), or use Force Attach to take the "
     "changed grid."),
    ("BonesMoved", "The skin bones moved",
     [r"skin bones moved since its particles were placed"],
     "The bones the Part is skinned to moved since its particles were placed, and the particles did not follow "
     "(the bones changed while the add-on was off, or the Part cannot move).",
     "Follow Bones (Part panel) moves the particles with their skin; a Part that cannot move keeps its file's "
     "places, so move the bones back."),
    ("MeshMoved", "The mesh moved under the particles",
     [r"the mesh moved under"],
     "The mesh the particles were anchored to changed shape since (sculpted, or imported again with the same "
     "faces).",
     "Refit to Mesh (General Tools) moves the particles with it; Anchor to Mesh keeps them where they are. If the "
     "particles already followed the bones that the mesh was changed for, anchor them again instead."),
    ("AnchorsStale", "The anchors no longer hold",
     [r"other faces than when its particles were anchored", r"its anchor mesh .* is gone",
      r"its particles changed since they were anchored"],
     "The mesh the particles were anchored to has other faces now, or is gone.",
     "Anchor to Mesh anchors them to the mesh as it is; Fit to Mesh moves them from a copy of the old mesh."),
    ("NoMeshPairs", "No mesh pairs",
     [r"pairs with one in"],
     "Fit to Mesh pairs the meshes of the two collections by their material names (after \"__\"), and found none.",
     "Pick the collections of the same character's meshes, or rename the target's meshes to the source's material "
     "names; with Use Its Armature on and another armature, the particles go by its bones alone."),
    ("NoMeshToProject", "No mesh to project onto",
     [r"no mesh skinned to its bones"],
     "Project onto Mesh takes the faces of the armature's mesh collection skinned to the Part's bones, and there "
     "are none.",
     "Import the character's .mesh with RE Mesh Editor, with the cloth on its armature, and check that the cloth "
     "mesh is weighted to these bones."),
    ("PartGroupsDiffer", "The Part Groups do not fit",
     [r"Part Groups hold several Parts", r"in a Part Group of its own"],
     "Each Part Group is one part block of the file. Update Existing writes a file's part blocks onto a cloth made "
     "from bones only while each Group holds one Part, and RE4 writes each Part as a block of its own.",
     "Move the Parts so each Part Group holds one (Move, in the Part panel), or import the file as a new cloth."),
    ("SkinBoneNotWritten", "A skin bone cannot be written into the file",
     [r"its skin bone .* is no bone of", r"skin bones, no room for"],
     "A Part whose particles and faces stay its file's writes its painted skin into the file, which names each "
     "skin bone by its place in the mesh's skeleton.",
     "Paint the weights on bones of the cloth's armature (the mesh's skeleton), or move them to fewer bones."),
    ("LinksAfterStructureEdit", "Particles were added or removed",
     [r"leave Edit Mode once, then edit its links"],
     "The links are numbered by the particles as they were when Edit Mode was entered.",
     "Leave Edit Mode once (Tab) so the Part takes its new particles, then edit its links."),
    ("ChainsInvalid", "The bones make no chains",
     [r"below the selected", r"a column must not branch", r"at least two chains", r"at least two bones"],
     "Create Part takes chains of bones, one column each, from their first bones down.",
     "Select only the first bone of each chain (or whole chains with Selected Bones), at least two chains of "
     "two bones, none branching."),
]


def explain(message: str):
    """The entry that names ``message``: (name, title, what, fix), else None."""
    for name, title, patterns, what, fix in TABLE:
        if any(re.search(p, message, re.IGNORECASE) for p in patterns):
            return name, title, what, fix
    return None


def show(message: str, title: str = "") -> str:
    """Pop the named problem of ``message`` up beside the report (not in background Blender); return its name, ""
    when it has none."""
    found = explain(message)
    if found is None:
        return ""
    name, heading, what, fix = found
    if not bpy.app.background and bpy.context.window_manager is not None:
        def draw(menu, _context):
            col = menu.layout.column(align=True)
            for text in (message, "", what, "", "How to fix:", fix):
                for line in textwrap.wrap(text, 90) or [""]:
                    col.label(text=line)
        try:
            bpy.context.window_manager.popup_menu(draw, title=f"{title or heading} ({name})", icon="ERROR")
        except RuntimeError:                           # no window to show it in
            pass
    return name
