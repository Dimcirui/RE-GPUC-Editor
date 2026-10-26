"""The editor's interface for Wilds-Dynamics (that project's editor interface, item I3):
a GPUC collection as its .gpuc bytes in memory, built as the Export operator builds it, and the file on disk it
stands for (where the .mesh and the prefab are found beside it). Nothing here writes a file or changes the scene;
the editor does not import Wilds-Dynamics (I5)."""
from . import fileio, props

API_VERSION = 1
COLLECTION_TYPES = (props.COLLECTION_TYPE,)


class ExportError(Exception):
    """The collection cannot be exported; the message says why, for the user."""


def export_bytes(collection, version=None) -> bytes:
    """The GPUC collection as a .gpuc (version: the file extension's number, e.g. 241111760; None: the cloth's own
    game). A Keep Structure cloth over its stored file, the driven joints as imported; a cloth made from bones, joints
    from its mesh."""
    if not props.is_gpuc_collection(collection):
        raise ExportError(f"{getattr(collection, 'name', collection)} is not a GPUC collection")
    if props.header(collection) is None:
        raise ExportError(f"{collection.name} has no header")
    game = f".{version}" if version is not None else ""
    try:
        data, _ = fileio.build_bytes(collection, None, "FILE", game)
    except fileio.ExportError as e:
        raise ExportError(f"{collection.name}: {e}") from e
    return data


def file_path(collection) -> str:
    """The .gpuc file on disk the collection stands for: the one it was last exported to, else imported from; ""
    for a cloth never written or read (its .mesh and prefab cannot be found then)."""
    head = props.header(collection)
    if head is None:
        return ""
    return str(head.get(fileio.EXPORTED_KEY, "") or head.get(fileio.SOURCE_KEY, ""))
