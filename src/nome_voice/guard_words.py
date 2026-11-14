"""Accidental-trigger guard: extra "listen for" words that never fire a command.

With only a handful of command phrases in the grammar, Vosk has to explain every
sound with one of them, so ordinary speech like "the system is updating" comes back
as "system update" with full confidence. Giving the recognizer everyday words and
word forms of the command words ("reloading", "terminals") to choose from lets it
say what was really heard, and those words never match a command.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

GUARD_LEVELS = ("off", "standard", "strong")
DEFAULT_GUARD_LEVEL = "strong"

# Everyday English words, chat words, and common gaming words. Words the speech model
# does not know are dropped at runtime, and so is anything that could collide with a
# command phrase (see build_guard_words).
COMMON_WORDS = """
a about above across act action actually add added adding after afternoon again against
age ago agree ahead aim air alive all allow allowed almost alone along already alright
also although always am amazing among amount an and angry animal another answer any
anybody anymore anyone anything anyway anywhere apart apple april are area aren't arm
armor arms army around arrive arrow arrows art as ask asked asking at ate attack
attacked attacking august aunt autumn available avoid awake away awesome awful baby back
bad badly bag ball bank bar base basically battle be beach bear beat beautiful became
because become bed been beer before began begin beginning behind being believe below
belt beside best bet better between big bigger biggest bike bird birthday bit bite black
blade blood blow blue board boat body bomb bone book books boom boost bored boring born
boss both bottle bottom bought bounce bow box boy boys brain brave bread break breakfast
breath bridge bright bring broke broken brother brought brown buddy bug build building
built bullet bullets burn bus business busy but buy buying by bye cake call called
calling came camera camp can can't cancel cannot cap captain car card care careful
carry case cash castle cat catch caught cause cell center certain chair chance change
changed changing charge chat cheap check checked checking cheese chest chicken chief
child children choice choose chose church circle city class classic clean clear clearly
click climb clock close closed closer clothes cloud club coffee coin coins cold collect
color come comes coming common company complete completely computer control cook cool
copy corner correct cost could couldn't count country couple course cousin cover crash
crazy cream create crew cross crowd crown cry cup cut cute dad damage damn dance danger
dangerous dark date daughter day days dead deal dear death december decide decided deep
defend definitely degree delete deliver dependent depends desk did didn't die died
different difficult dinner direction dirty discord distance do doctor does doesn't dog
doing dollar dollars don't done door double down download dozen drag dragon draw dream
dress drink drive driver drop dropped drove drunk dry dude dumb during dust duty each
ear early earn ears earth easier easily east easy eat eaten eating edge egg eight
eighteen eighty either else empty end ending enemies enemy energy enjoy enough enter
entire equal escape even evening event ever every everybody everyone everything
everywhere exactly example except excited excuse exit expect expensive experience
explain extra eye eyes face fact fail fair fall fallen false family famous fan far farm
fast faster father favorite fear february feel feeling feet fell felt few field fifteen
fifth fifty fight fighting figure fill final finally find fine finger finish finished
fire first fish five fix flag flat floor flower fly follow food foot for force forest
forever forget forgot fork form forty forward found four fourth free freeze friday
friend friends from front fruit full fun funny further future gain game games garden
gas gate gave gear general get gets getting ghost gift girl girls give given gives glad
glass go goal god goes going gold golden gone gonna good got gotta gotten grab grass
gray great green ground group grow guard guess guide gun guns guy guys had hair half
hall hand hands hang happen happened happens happy hard has hat hate have haven't
having he he's head heal healer health hear heard heart heavy held hell hello help
here here's hero hey hi hidden hide high hill him himself his history hit hold hole
home honest hope horse hospital hot hotel hour hours house how how's however huge
human hundred hungry hunt hurry hurt husband i i'd i'll i'm i've ice idea if ill
important in inside instead interesting into invite is isn't island it it's item items
its itself jacket january job join joke july jump june just keep kept key keys kick
kid kids kill killed killing kind king kitchen knew knife know known lady lag lake
land language large last late later laugh law lay lazy lead learn least leave leaving
led left leg legs less let let's letter level levels library lie life lift light
like liked line lion list listen little live lives living lock long longer look looked
looking loose lord lose losing loss lost lot lots loud love lovely low luck lucky lunch
mad made magic mail main make makes making man manage many map maps march mark market
matter maximum may maybe me meal mean means meant meat medium meet meeting member men
menu message met metal middle might mile milk million mind mine minute minutes mirror
miss missed missing mission mistake mode moment monday money monster month months moon
more morning most mostly mother mountain mouse mouth move moved movie moving much mud
music must my myself name names near nearly neck need needed needs never new news next
nice night nine nineteen ninety no nobody noise none noon nope normal north nose not
note nothing notice november now number numbers nurse ocean october odd of off offer
office often oh oil okay old older on once one online only onto open opened opposite
or orange order other others our out outside over own owner page paid pain paint pair
pants paper parent park part party pass past path pause pay peace pen people perfect
perhaps person phone pick picked picture piece pink pizza place plan plane planet plant
play played player players playing please point pole police pool poor pop popular
position possible potato power powerful practice present pretty price print probably
problem problems program pull punch purple push put queen question questions quick
quickly quiet quit quite race rain raise ran rank rather reach read ready real really
reason recent red relax remember repeat rest return rich ride right ring rise river road
rock role roll roof room rope round row rule rules run running rush sad safe said sale
same sand saturday save saved saw say saying says scared school score sea search season
seat second secret see seem seen sell send sense sent september serious seriously serve
server set seven seventeen seventy several shake shall shape share she she's sheep
shield shine ship shirt shoe shoes shoot shooting shop short shot should shoulder
shouldn't shout show shut sick side sign silly silver simple since sing single sir
sister sit six sixteen sixty size skill skills skin sky sleep slow slowly small smart
smell smile snake snow so soft soldier some somebody someone something sometimes
somewhere son song songs soon sorry sort sound south space speak special speed spell
spend spent spin spot spring square stage stairs stand star stars start started state
station stay steal step stick still stone stood store storm story straight strange
street strong student studio stuff stupid style such sugar summer sun sunday super sure
surprise sweet swim sword table tail take taken takes taking talk talked talking tall
tank taste teach teacher team tear tell ten than thank thanks that that's the their
them then there there's these they they're thing things think thinking third thirteen
thirty this those though thought thousand three threw through throw thursday ticket
tight time times tiny tired to today together told tomorrow tonight too took tooth
total touch tough toward tower town toy track trade train trap travel treasure tree
trees trick tried trip trouble truck true trust truth try trying tuesday turn turned
twelve twenty twice two type ugly uncle under understand unit until up upon upstairs
us use used useful usual usually valley very video view village visit voice wait
waiting wake walk walked walking wall want wanted wants war warm was wash wasn't watch
watching water wave way we we're weak wear weather wednesday week weekend weeks weird
welcome well went were weren't west wet what what's whatever wheel when where which
while white who who's whole why wide wife wild will win wind window wing winner winter
wish with without woke woman women won't wonder wood word words wore work worked
working world worry worse worst would wouldn't wow write wrong yeah year years yell
yellow yep yes yesterday yet you you'll you're you've young your yours yourself zero
zone ammo armor buff camp crouch dodge dungeon equip flank grenade headshot inventory
loot mana nade noob potion quest raid revive scope sniper sprint squad stamina teammate
zoom
"""

# When a command uses one of these words, the other spellings would steal it.
HOMOPHONE_GROUPS = (
    ("to", "too", "two"), ("for", "four", "fore"), ("there", "their", "they're"),
    ("right", "write", "rite"), ("know", "no"), ("knew", "new"), ("one", "won"),
    ("by", "buy", "bye"), ("here", "hear"), ("see", "sea"), ("be", "bee"),
    ("i", "eye", "aye"), ("hour", "our"), ("wait", "weight"), ("way", "weigh"),
    ("week", "weak"), ("meet", "meat"), ("peace", "piece"), ("plane", "plain"),
    ("sale", "sail"), ("son", "sun"), ("tail", "tale"), ("wear", "where", "ware"),
    ("which", "witch"), ("whole", "hole"), ("road", "rode", "rowed"), ("made", "maid"),
    ("mail", "male"), ("pair", "pear"), ("red", "read"), ("break", "brake"),
    ("cell", "sell"), ("cent", "sent", "scent"), ("dear", "deer"), ("die", "dye"),
    ("fair", "fare"), ("heal", "heel"), ("high", "hi"), ("night", "knight"),
    ("not", "knot"), ("pain", "pane"), ("real", "reel"), ("seen", "scene"),
    ("so", "sew", "sow"), ("some", "sum"), ("steal", "steel"), ("threw", "through"),
    ("would", "wood"), ("your", "you're"), ("ate", "eight"), ("allowed", "aloud"),
    ("bear", "bare"), ("blew", "blue"), ("board", "bored"), ("flew", "flu"),
    ("great", "grate"), ("passed", "past"), ("war", "wore"), ("waist", "waste"),
    ("whether", "weather"), ("root", "route"), ("lead", "led"), ("in", "inn"),
    ("none", "nun"), ("or", "oar", "ore"), ("tea", "tee"), ("toe", "tow"),
    ("pause", "paws"), ("rain", "reign"), ("stair", "stare"), ("hire", "higher"),
    ("pseudo", "psuedo", "sudo"),
)

_INFLECTION_SUFFIXES = ("s", "es", "ed", "d", "ing", "er", "ers", "ings", "ly")


def _word_set(text: str) -> set[str]:
    return {word for word in text.split() if word}


def common_words() -> list[str]:
    return sorted(_word_set(COMMON_WORDS))


def inflection_candidates(word: str) -> set[str]:
    """Possible word forms of a command word ("reload" -> "reloads", "reloading", ...)."""
    if not word.isalpha() or len(word) < 3:
        return set()
    stems = {word}
    if word.endswith("e"):
        stems.add(word[:-1])
    if word.endswith("y") and len(word) > 3:
        stems.add(word[:-1] + "i")
    vowels = set("aeiou")
    if (
        len(word) >= 3
        and word[-1] not in vowels | {"w", "x", "y"}
        and word[-2] in vowels
        and word[-3] not in vowels
    ):
        stems.add(word + word[-1])
    candidates = {stem + suffix for stem in stems for suffix in _INFLECTION_SUFFIXES}
    candidates |= {"un" + word, "re" + word, word + "s"}
    candidates.discard(word)
    return candidates


def _within_one_edit(first: str, second: str) -> bool:
    """Damerau-Levenshtein distance <= 1 (one insert, delete, swap, or adjacent swap)."""
    if first == second:
        return True
    len_a, len_b = len(first), len(second)
    if abs(len_a - len_b) > 1:
        return False
    if len_a == len_b:
        diffs = [index for index in range(len_a) if first[index] != second[index]]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            i, j = diffs
            return first[i] == second[j] and first[j] == second[i]
        return False
    shorter, longer = (first, second) if len_a < len_b else (second, first)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1:] == shorter:
            return True
    return False


def _homophones(word: str) -> set[str]:
    found: set[str] = set()
    for group in HOMOPHONE_GROUPS:
        if word in group:
            found.update(group)
    found.discard(word)
    return found


def _collides_with_command_word(candidate: str, command_word: str) -> bool:
    """Would this extra word risk stealing (part of) a command word?"""
    if candidate == command_word:
        return True
    if candidate in _homophones(command_word):
        return True
    # pieces of a command word: "up" + "date" -> "update", "desk" + "top" -> "desktop"
    if len(candidate) < len(command_word) and (
        command_word.startswith(candidate) or command_word.endswith(candidate)
    ):
        return True
    # near spellings are usually near sounds: "for"/"four", "pseudo"/"psuedo"
    return len(command_word) >= 2 and _within_one_edit(candidate, command_word)


def build_guard_words(
    phrases: Iterable[str],
    is_known_word: Callable[[str], bool],
    level: str = DEFAULT_GUARD_LEVEL,
    extra_words: Iterable[str] = (),
) -> list[str]:
    """Return guard words to add to the grammar for these command phrases.

    level "off": nothing. "standard": word forms of the command words only.
    "strong": word forms plus everyday words.
    """
    if level not in GUARD_LEVELS or level == "off":
        return []

    command_words = {word for phrase in phrases for word in phrase.split()}
    decoys: set[str] = set()
    for command_word in command_words:
        for candidate in inflection_candidates(command_word):
            if candidate in command_words:
                continue
            if any(candidate in _homophones(word) for word in command_words):
                continue
            if is_known_word(candidate):
                decoys.add(candidate)

    words = set(decoys)
    if level == "strong":
        pool = _word_set(COMMON_WORDS) | _word_set(" ".join(extra_words))
        for candidate in pool:
            if candidate in words or candidate in command_words:
                continue
            if any(_collides_with_command_word(candidate, word) for word in command_words):
                continue
            if is_known_word(candidate):
                words.add(candidate)

    words.discard("[unk]")
    return sorted(words)
