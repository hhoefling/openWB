from pathlib import Path

from helpermodules import log

RAMDSIK_PATH = Path(__file__).resolve().parents[2] / "ramdisk"


def is_ramdisk_in_use() -> bool:
    """ prüft, ob die Daten in der Ramdisk liegen (v1.x), sonst wird mit dem Broker (2.x) gearbeitet.
    """
    return (RAMDSIK_PATH / "bootinprogress").is_file()

# write value to file in ramdisk


def write_to_ramdisk(filename: str, content: str) -> None:
    with open(str(RAMDSIK_PATH) + "/" + filename, "w") as file:
        file.write(content)


# read value from file in ramdisk
def read_from_ramdisk(filename: str) -> str:
    try:
        with open(str(RAMDSIK_PATH) + "/" + filename, 'r') as file:
            return file.read()
    except FileNotFoundError as e:
        log.MainLogger().exception("Error reading file '" + filename + "' from ramdisk!")
        raise e
