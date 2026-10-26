"""Give Wilds-Dynamics a GPUC collection's .gpuc bytes, built as the Export operator builds them, and its file path.

Maintenance constraints: nothing here writes a file or changes the scene; the editor never imports Wilds-Dynamics,
which finds this module in ``sys.modules``."""
from . import fileio, props

API_VERSION = 1
COLLECTION_TYPES = (props.COLLECTION_TYPE,)


class ExportError(Exception):
    """The collection cannot be exported; the message says why, for the user."""


def export_bytes(collection, version=None) -> bytes:
    """Return the collection as .gpuc bytes; raise ``ExportError`` (message for the user) when it cannot be built.

    ``version``: the file extension's number, e.g. 241111760; None for the cloth's own game. A Keep Structure cloth
    is written over its stored file with the driven joints as imported; a cloth made from bones takes its joints
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
    """Return the .gpuc path the collection stands for (last exported to, else imported from), beside which its
    .mesh and prefab are found; "" for a cloth never written or read."""
    head = props.header(collection)
    if head is None:
        return ""
    return str(head.get(fileio.EXPORTED_KEY, "") or head.get(fileio.SOURCE_KEY, ""))
