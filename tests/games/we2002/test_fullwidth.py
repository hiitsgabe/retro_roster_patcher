# ruff: noqa: E501  (the pinned budgets quote the disc's strings whole)
"""The full-width and ASCII menu tables in `fullwidth_text`, and the pass
that writes them over the community patch's English.

The community PPF may not enter the repository, so the byte budget of every
string it leaves on the disc was read off it once (the narrowest field each
English string lands in) and pinned here, like `test_menu_translations`.
"""

import pytest

from retro_roster_patcher.games.we2002.translations.we2002 import fullwidth
from retro_roster_patcher.games.we2002.translations.we2002.fullwidth_text import ASCII, FULLWIDTH

LANGS = ("es", "fr", "pt")

# English (as the patched disc holds it) -> narrowest byte budget on the disc
_FULLWIDTH_BUDGETS = {
    "\n\nLoad \n\nExit": 38,
    "\n\nPlay \n\nExit": 35,
    "\n\u3000Play\n\n  Save \n\n  Exit": 50,
    "1-2 Practice": 26,
    "Aborted": 49,
    "Aceleration": 22,
    "Africa Cup\nAfrica NT": 79,
    "Age": 18,
    "Agression": 22,
    "Already used": 30,
    "America Cup\nAmerica NT": 120,
    "Are you sure?\n\x0b\u3000\u3000\u3000\u3000Yes\n\x0b\u3000\u3000\u3000\u3000No": 55,
    "Asia Cup\nAsia NT": 100,
    "Attack Practice": 50,
    "Audio": 34,
    "Auto/Manual ■ Change": 58,
    "Autosave\nOn/Off": 44,
    "BGM": 34,
    "Body": 18,
    "Body Balance": 26,
    "Boots   ■ Turn": 50,
    "Buy players\nMaximum of 2\nPlayers\nby Game": 114,
    "Buy\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000No.\n\n\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 70,
    "Callnames": 38,
    "Camera": 18,
    "Canc.": 10,
    "Can’t save": 26,
    "Change\nShirt Number\n": 94,
    "Change Strategy \n": 58,
    "Change?\n● Yes ★ No": 86,
    "Checking...": 26,
    "Choose Number": 27,
    "Choose team...\n■ Show substitute": 66,
    "Commentary": 30,
    "Complete": 16,
    "Configure the Game": 62,
    "Confirm": 26,
    "Confirm Changes?\nSTART Yes ★ NO": 110,
    "Confirm Copies": 58,
    "Confirm Delete": 46,
    "Continue": 18,
    "Copy Strategy \n": 79,
    "Creat. File": 24,
    "Curve": 30,
    "Defaults": 16,
    "Defence": 30,
    "Delete\nSTART Confirm ★ Cancel": 71,
    "Delete Block\nSTART Confirm ★ Cancel": 71,
    "Delete Player": 46,
    "Delete Strategy \n": 66,
    "Delete?": 34,
    "Delete?\n● Yes ★ No": 66,
    "Deleting\nDon’t move Mem Car": 50,
    "Display Switches": 38,
    "Done": 10,
    "Dribble": 30,
    "ERROR": 16,
    "Edit Names": 67,
    "Edit Numbers": 79,
    "Edit Player": 106,
    "Elabor. Strategy": 34,
    "Empty": 32,
    "Empty Space": 34,
    "Error\n● REPEAT ★ CANCEL": 70,
    "Error\n● Repeat ★ Cancel": 69,
    "Error with the Memory Card": 80,
    "Error with the Memory Card\n● Repeat ★ Cancel": 114,
    "Exit": 8,
    "Exit Training": 26,
    "Exit from Match": 50,
    "Exit?": 46,
    "Exit? \n\n\nYes \n\nNo": 74,
    "Extra Time?\nYes/No": 50,
    "Foot": 22,
    "Formation set": 46,
    "Formation used": 30,
    "Free Kick Practice": 46,
    "Friendly Match": 44,
    "Full": 16,
    "Game": 10,
    "Games": 14,
    "Golden Goal?\nYes/No": 101,
    "Graph": 12,
    "Group": 14,
    "Groups\nNumbers/Letters": 50,
    "Hair Colour   ■ Turn": 47,
    "Hair Face Colour   ■ Turn": 50,
    "Head Accuracy": 30,
    "Height": 18,
    "Home and Away\nNo/Yes": 115,
    "Imposible to do": 38,
    "Information\nabout\nPlayers": 74,
    "Injured Player": 38,
    "Insert Player": 98,
    "International Cup\nWorld NT": 118,
    "Japan - Korea": 30,
    "Joypad set": 70,
    "Jump Power": 26,
    "Kind of Cup\nCup/League/Group League": 76,
    "Kind of Face Hair   ■ Turn": 46,
    "Kind of Hair   ■ Turn": 46,
    "Konami Cup\nFree choice": 89,
    "League        Internat.": 38,
    "Left Corner Kicks": 46,
    "Level\nN/H/E": 24,
    "Load": 10,
    "Load Configuration": 54,
    "Load Cup": 66,
    "Load League": 66,
    "Load Master League": 82,
    "Load Master League Team": 81,
    "Load/Save Strategy": 42,
    "Loading Data in\nthe Memory Card": 98,
    "Loading Strategy...": 51,
    "Loading...": 51,
    "Loading...\nDon’t remove Memory Card": 67,
    "Loading...\nDon’t remove the Memory Card": 75,
    "Make your own Team": 94,
    "Master League": 26,
    "Match Length\n10/15/20/25/30/5 MIN": 91,
    "Memory Card": 54,
    "Memory Card is full \n★ Repeat": 104,
    "Memory Card is used\nto load Options": 106,
    "Memory Card is used\nto save Options": 90,
    "Modify": 38,
    "Modify stored": 52,
    "Modify?\n● Yes ★ No": 57,
    "Must have 2 free\nBlocks\n● Repeat\n★ Cancel": 98,
    "Names": 26,
    "Nation": 18,
    "Nationals": 46,
    "New": 26,
    "New Cup": 26,
    "New League": 26,
    "New Master League": 68,
    "No Data": 32,
    "No Data\nof WINNING ELEVEN 2002": 108,
    "No Data found": 68,
    "No Data stored": 84,
    "No Memory Card": 28,
    "No Memory Card\n★ Try again": 51,
    "No Option": 70,
    "No Options": 30,
    "No strategy\n★ Cancel": 67,
    "Normal Far": 30,
    "Normal Mid.": 30,
    "Normal Near": 30,
    "Number of Teams": 40,
    "Offence": 30,
    "Operation succesfully\ncompleted": 78,
    "Options": 28,
    "Outside": 42,
    "Overhead Far": 30,
    "Overhead Mid.": 30,
    "Overhead Near": 30,
    "POS": 6,
    "Pass": 18,
    "Pass Acc.": 22,
    "Penalty Kicks": 38,
    "Penalty Kicks?\nYes/No": 63,
    "Place to save?": 38,
    "Play Friendly and\nPenalty Kicks": 75,
    "Play an International\nLeague": 98,
    "Play with All Stars": 50,
    "Play with the best\nClubs of the World": 107,
    "Play with the best\nNational Teams of the World": 106,
    "Play? \n\n\nYes \n\nNo": 66,
    "Player Name": 42,
    "Pos": 10,
    "Position": 22,
    "Position of Team\n\nWon:3pts\nDraw:1pt\nLost:0pt": 110,
    "Position\u3000\u3000\u3000\u3000Buy\n\nResult\u3000\u3000\u3000\u3000\u3000\u3000Scorer\n\nW/L\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Pass\n\nW/L2\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u30001 Div\n\nGraph\u3000\u3000\u3000\u3000\u3000\u3000\u3000Table\n\nPlayers\u3000\u3000\u3000\u3000\u3000Games\u3000\u3000\n\n\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 268,
    "Position\u3000\u3000\u3000\u3000Players\n\nResult\u3000\u3000\u3000\u3000\u3000\u3000Scorer\n\nW/L\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Pass\n\nW/L2\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Table\u3000\u3000\n\nGraph\u3000\u3000\u3000\u3000\u3000\u3000\u3000Games\u3000\u3000\n\n\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 234,
    "Position\u3000\u3000\u3000\u3000Scorer\n\nW/L\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Pass\n\nPlayers\u3000\u3000\u3000\u3000\u3000Table\n\n\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 158,
    "Position\u3000\u3000\u3000\u3000\u3000Buy\n\nResult\u3000\u3000\u3000\u3000\u3000\u3000Scorer\n\nW/L\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Pass\n\nW/L2\u3000\u3000\u3000\u3000\u3000\u3000\u30002 Div\n\nGraph\u3000\u3000\u3000\u3000\u3000\u3000\u3000Table\u3000\n\nPlayers\u3000\u3000\u3000\u3000\u3000\u3000Games\u3000\n\n\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 268,
    "Position\u3000\u3000\u3000\u3000\u3000Graph\n\nResult\u3000\u3000\u3000\u3000\u3000\u3000Table\u3000\u3000\n\nW/L\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Games\u3000\u3000\n\nW/L2\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 154,
    "Position\u3000\u3000\u3000\u3000\u3000Graph\n\nResult\u3000\u3000\u3000\u3000\u3000\u3000\u3000Table\u3000\u3000\n\nW/L\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Games\u3000\u3000\n\nW/L2\u3000\u3000\u3000\u3000\u3000\u3000\u3000\u3000Yes": 154,
    "Press START to load": 73,
    "Press START to save": 61,
    "Press ■\nAutomatic/Manual": 100,
    "Radar": 46,
    "Ready": 14,
    "Repeat": 22,
    "Replay": 50,
    "Response": 22,
    "Restore Defaults": 31,
    "Result": 14,
    "Results of Team": 58,
    "Return": 30,
    "Right Corner Kicks": 46,
    "START Confirm\n★    Cancel": 66,
    "START format\n● Try\n★ Continue": 70,
    "START to create\n● Search\n★ Cancel": 86,
    "Save": 10,
    "Save\nSTART Confirm ★ Cancel": 70,
    "Save Changes": 46,
    "Save Configuration": 87,
    "Save Data": 83,
    "Save Strategy. \nSTART Save ★  Cancel": 87,
    "Save?\nSTART Yes ★ No": 95,
    "Save?\n● Yes ★ No": 83,
    "Saving\nDon’t move Mem. Card": 51,
    "Saving Data...": 73,
    "Saving...": 51,
    "Score": 34,
    "Scorer": 16,
    "Screen set": 40,
    "Searching...\nDon’t remove the Mem. Card": 74,
    "Season\nFull/Half": 68,
    "Select Callname. ■ ▲ Listen\nL1 L2 R1 R2 Page": 86,
    "Select Kits": 81,
    "Select Mem Card\n● Yes ★ Cancel": 55,
    "Select Mem. Card": 32,
    "Select Player": 34,
    "Select Stadium": 35,
    "Select Team\nR1+  ■ Show Substitute": 98,
    "Select Team\n■  Show Substitute": 83,
    "Select Team\n■ Show Substitute": 154,
    "Select Team to copy": 62,
    "Select Team to delete": 58,
    "Select Team to load": 58,
    "Select Team to save": 58,
    "Select a Place": 44,
    "Select number of Players": 81,
    "Select the Data": 42,
    "Select the Joypad\n● Yes ★ Cancel": 62,
    "Select the Memory Card": 59,
    "Select ● Load": 46,
    "Select ● Save ■ Delete": 54,
    "Select...": 32,
    "Shirt": 26,
    "Shot Accuracy": 26,
    "Shot Power": 26,
    "Show\nSchedule\nTable": 53,
    "Show Graphic of Team\n\n● Show/Not\nshow\n(Max:6)": 102,
    "Show Team\nGroups": 98,
    "Show the 32\nbest Assisters": 90,
    "Show the 32\nbest Scorers": 70,
    "Skills": 30,
    "Skin Colour   ■ Turn": 47,
    "Sound Effect": 34,
    "Sound set": 30,
    "Space enough\n": 62,
    "Speed": 22,
    "Speed set -/+": 30,
    "Speed slider": 34,
    "Stamina": 22,
    "Start!": 18,
    "Strategy": 46,
    "Strategy loaded\n": 97,
    "Succesfully completed": 87,
    "TV": 42,
    "Table": 14,
    "Table\u3000\u3000\u3000\u3000\u3000\u3000\u3000Pass \n\nPlayers\u3000\u3000\u3000\u3000\u3000Games\u3000\n\nScorer\u3000\u3000\u3000\u3000\u3000\u3000Yes": 116,
    "Technique": 26,
    "Time": 38,
    "Time\nRandom/Day/Night": 61,
    "Train to improve": 110,
    "Training": 42,
    "Training set": 26,
    "Undo": 18,
    "Updating, please wait...": 90,
    "Use Joypad to move\n● Accept ★ Cancel": 83,
    "Used - Options": 30,
    "Used yet": 30,
    "Verify\nTable of\nGames": 94,
    "Visual": 30,
    "W/L": 6,
    "W/L2": 8,
    "Weather\nRandom/SS/SR/LS/LR": 55,
    "Which one to delete?": 50,
    "Wide": 34,
    "Won\nLost\nShow Result\n": 70,
    "Won/Lost\n\n● Won\n▲ Draw\n★ Lost": 78,
    "Zoom": 26,
    "● Change ★ Cancel": 38,
    "● Select Replay": 34,
    "\u3000  ▲ Reset\n\u3000  ★ Cancel\n\u3000  ● OK": 54,
}

_ASCII_BUDGETS = {
    "   LEFT": 7,
    "  RIGHT": 7,
    " 5 MIN  SITUAT.": 15,
    "$DIVISION 2": 12,
    "1-2 SHOOT PRAC.": 15,
    "1st STAGE": 11,
    "2nd STAGE": 11,
    "ARGEN.CLASS": 11,
    "BGM": 7,
    "BRAZIL CLAS": 11,
    "Bieloru": 7,
    "CHANGEABLE": 15,
    "COM": 3,
    "CPU STRAT.": 11,
    "Cameroo": 7,
    "Clas.France": 11,
    "Clas.Nether": 11,
    "Class.Argentina": 15,
    "Class.Italy": 11,
    "DEFENSE TYPE": 15,
    "DEUTCH.CLAS": 11,
    "ENGLAND CLA": 11,
    "Ivory Coast": 11,
    "Kazakhs": 7,
    "Luxembu": 7,
    "MEM CAR": 7,
    "MEM CARD SLOT": 15,
    "Macedon": 7,
    "NORMAL FAR": 11,
    "NORMAL MID": 11,
    "NORMAL NEAR": 11,
    "Netherl": 7,
    "New Zeland": 10,
    "Nor.Ireland": 11,
    "POSITION": 11,
    "S.Afric": 7,
    "SAUDI ARABI": 11,
    "SOU": 3,
    "Saudi Arabi": 11,
    "Slovaki": 7,
    "Sloveni": 7,
    "Thailan": 7,
    "UNUSED": 11,
    "USING....  SAVING": 25,
    "Uzbekistan": 10,
    "YUGOSLAVIA": 10,
    "Yugoslavia": 10,
}


def _budget_ok(text: str, budget: int, encode) -> bool:
    """Some `|` choice of `text` encodes within `budget` bytes."""
    for choice in text.split("|"):
        raw = encode(choice)
        if raw is not None and len(raw) <= budget:
            return True
    return False


def test_every_full_width_entry_is_on_the_disc_and_fits_its_field():
    assert set(FULLWIDTH) == set(_FULLWIDTH_BUDGETS)
    for english, budget in _FULLWIDTH_BUDGETS.items():
        for lang in LANGS:
            text = FULLWIDTH[english][lang]
            assert _budget_ok(text, budget, fullwidth._encode), (english, lang, text, budget)


def test_every_ascii_entry_is_on_the_disc_and_fits_its_field():
    assert set(ASCII) == set(_ASCII_BUDGETS)
    for english, budget in _ASCII_BUDGETS.items():
        for lang in LANGS:
            text = ASCII[english][lang]
            assert fullwidth._rewrite_ascii(b" " * budget, text) is not None, (english, lang)


def _full_width(text: str) -> bytes:
    """`text` the way the community patch writes it: full-width letters, ASCII spaces."""
    return fullwidth._encode(text)


SECTOR, DATA = 2352, 24


def _disc_and_ppf(tmp_path, strings: dict[int, bytes]):
    """A two-sector image with each string NUL-terminated at its user-data offset,
    and a PPF2 whose records start at those offsets."""
    image = bytearray(SECTOR * 2)
    records = bytearray()
    for at, raw in strings.items():
        offset = DATA + at
        image[offset : offset + len(raw)] = raw
        records += offset.to_bytes(4, "little") + bytes([1]) + raw[:1]
    ppf = tmp_path / "w202-english.ppf"
    ppf.write_bytes(b"PPF20" + b"\x01" + b"\0" * 50 + b"\0" * 4 + b"\0" * 1024 + records)
    return image, str(ppf)


def _read(image, at, size):
    return bytes(image[DATA + at : DATA + at + size])


def test_full_width_english_is_rewritten_in_the_same_bytes(tmp_path):
    english = _full_width("Select the Memory Card") + b"   "
    image, ppf = _disc_and_ppf(tmp_path, {16: english})

    assert fullwidth.translate_image(image, ppf, "pt") == 1

    written = _read(image, 16, len(english))
    assert written == _full_width("Escolha o Memory Card").ljust(len(english))
    assert image[DATA + 16 + len(english)] == 0  # the terminator stays


def test_accents_are_dropped_since_the_font_has_none(tmp_path):
    english = _full_width("Penalty Kicks") + b"     "
    image, ppf = _disc_and_ppf(tmp_path, {16: english})

    fullwidth.translate_image(image, ppf, "pt")

    assert _read(image, 16, len(english)).startswith(_full_width("Penaltis"))


def test_a_shorter_choice_is_used_where_the_field_is_small(tmp_path):
    # "Exit" sits in a 4-letter field on the pause menu: "Salir" does not fit.
    image, ppf = _disc_and_ppf(tmp_path, {16: _full_width("Exit")})

    fullwidth.translate_image(image, ppf, "es")

    assert _read(image, 16, 8) == _full_width("Fin") + b"  "


def test_a_translation_that_does_not_fit_leaves_the_english(tmp_path):
    english = _full_width("Formation used")  # "Formacion usada" needs one more letter
    image, ppf = _disc_and_ppf(tmp_path, {16: english})

    assert fullwidth.translate_image(image, ppf, "es") == 0
    assert _read(image, 16, len(english)) == english


def test_a_japanese_leftover_after_the_english_is_kept(tmp_path):
    english = _full_width("Options") + b"   " + "。".encode("cp932")
    image, ppf = _disc_and_ppf(tmp_path, {16: english})

    fullwidth.translate_image(image, ppf, "pt")

    written = _read(image, 16, len(english))
    assert written == _full_width("Opcoes") + b"     " + "。".encode("cp932")


def test_whole_ascii_strings_are_translated_too(tmp_path):
    image, ppf = _disc_and_ppf(tmp_path, {16: b"MEM CAR", 64: b"DEFENSE TYPE   "})

    assert fullwidth.translate_image(image, ppf, "fr") == 2

    assert _read(image, 16, 7) == b"CARTE  "
    assert _read(image, 64, 15) == b"TYPE DEFENSE   "


def test_a_string_running_into_the_next_sector_is_left_alone(tmp_path):
    at = 2048 - 6
    english = _full_width("Select the Data")[:6]
    image, ppf = _disc_and_ppf(tmp_path, {at: english})
    image[SECTOR : SECTOR + DATA] = b"\x01" * DATA  # no NUL before the sector ends

    assert fullwidth.translate_image(image, ppf, "pt") == 0


@pytest.mark.parametrize("lang", LANGS)
def test_translate_file_rewrites_only_the_sectors_it_needs(tmp_path, lang):
    image, ppf = _disc_and_ppf(tmp_path, {16: _full_width("Select the Memory Card") + b"   "})
    path = tmp_path / "disc.bin"
    path.write_bytes(bytes(image))

    assert fullwidth.translate_file(str(path), ppf, lang) == 1

    expected = bytearray(image)
    fullwidth.translate_image(expected, ppf, lang)
    assert path.read_bytes() == bytes(expected)
