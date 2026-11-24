"""
Casual chat the bot answers itself: no AI, no tokens, instant.

Pat (23/11/2026): "I don't want it to say the same thing twice or have 5
things rotating". So every chat type is built from mix-and-match parts:
an opener, sometimes a middle, and a sign-off, each from its own pool.
Every part is a whole sentence on its own, so any combination reads fine,
and 40 x 40 x 30 parts make tens of thousands of different lines.

On top of that each person has a small memory (ChatMemory): the parts
they saw recently are skipped, and a full line they have already had is
never built for them again. That memory is a small capped JSON file so a
restart doesn't reset it.

The voice (Pat's picks, 23/11/2026): street punk crew mate plus hype gamer
buddy. Gritty, loyal, cheeky, loud. Swearing and crude jokes are fine (it's
adults). HARD LINES, always: no slurs, nothing about race, religion,
sexuality or gender identity, never about OTHER members, no real-world
threats, no self-harm jokes. No money, items, Xanax or battle stats either
(Pat's chat rule). Not kiwi: no "kia ora", no NZ slang.
"""

import os
import random
import re
import time
import zlib

# ---------------------------------------------------------------------------
# Per-person memory
# ---------------------------------------------------------------------------

MEMORY_FILE = 'chat_memory.json'
MEMORY_USERS = 400          # people kept (the faction is ~40, plus guests)
MEMORY_DAYS = 90            # someone silent this long is forgotten
MEMORY_LINES = 400          # full lines remembered per person
RECENT_PARTS = 20           # parts per slot a person won't see again soon


def _key(text):
    """Short stable id for a part or a line (crc32 is plenty for this)."""
    return format(zlib.crc32(text.encode('utf-8')), 'x')


class ChatMemory:
    """
    {user id: {'t': last used, 'parts': {slot: [part ids]}, 'lines': [line ids]}}.
    path=None keeps it in memory only (tests).
    """

    def __init__(self, path=None):
        self.path = path
        self.data = {}
        if path and os.path.exists(path):
            from utils.helpers import load_json
            loaded = load_json(path, {})
            self.data = loaded if isinstance(loaded, dict) else {}

    def user(self, uid):
        u = self.data.setdefault(str(uid), {})
        u.setdefault('parts', {})
        u.setdefault('lines', [])
        return u

    def recent(self, uid, slot):
        return set(self.user(uid)['parts'].get(slot, []))

    def seen_line(self, uid, line):
        return _key(line) in set(self.user(uid)['lines'])

    def remember(self, uid, slots, line, now=None):
        """slots: {slot: part text}. Saves (and prunes) right away."""
        u = self.user(uid)
        u['t'] = int(now or time.time())
        for slot, part in slots.items():
            lst = u['parts'].setdefault(slot, [])
            lst.append(_key(part))
            del lst[:-RECENT_PARTS]
        u['lines'].append(_key(line))
        del u['lines'][:-MEMORY_LINES]
        self.prune(now)
        self.save()

    def prune(self, now=None):
        now = now or time.time()
        cutoff = now - MEMORY_DAYS * 86400
        for uid in [k for k, v in self.data.items() if v.get('t', now) < cutoff]:
            del self.data[uid]
        if len(self.data) > MEMORY_USERS:
            oldest = sorted(self.data, key=lambda k: self.data[k].get('t', 0))
            for uid in oldest[:len(self.data) - MEMORY_USERS]:
                del self.data[uid]

    def save(self):
        if self.path:
            from utils.helpers import save_json
            save_json(self.path, self.data)


_MEMORIES = {}


def memory_for(data_dir):
    """One ChatMemory per data folder, loaded once (None = in memory only)."""
    if not data_dir:
        return _MEMORIES.setdefault(None, ChatMemory(None))
    path = os.path.join(data_dir, MEMORY_FILE)
    if path not in _MEMORIES:
        _MEMORIES[path] = ChatMemory(path)
    return _MEMORIES[path]


def pick_part(pool, slot, uid, mem, rng):
    """A part from `pool` this person hasn't seen lately (any, if all were)."""
    recent = mem.recent(uid, slot) if mem else set()
    fresh = [p for p in pool if _key(p) not in recent]
    return rng.choice(fresh or pool)


def build_line(slots, uid, mem, rng, middle_chance=0.6, tries=40):
    """
    slots: [(slot name, pool or None)] in order; a pool marked optional is
    given as ('name?', pool) and used `middle_chance` of the time. Returns
    the joined line, never one this person already had (when avoidable).
    """
    line, used = '', {}
    for _ in range(tries):
        used = {}
        for name, pool in slots:
            if not pool:
                continue
            if name.endswith('?'):
                if rng.random() >= middle_chance:
                    continue
                name = name[:-1]
            used[name] = pick_part(pool, name, uid, mem, rng)
        line = ' '.join(used.values())
        if not mem or not mem.seen_line(uid, line):
            break
    if mem:
        mem.remember(uid, used, line)
    return line


# ---------------------------------------------------------------------------
# The pools. Every entry is a whole sentence (or a whole exclamation), so
# any opener + middle + closer combination is grammatical.
# ---------------------------------------------------------------------------

# Things the bot is up to / how life is in the armoury. Shared by most kinds.
MIDDLES = [
    "Just finished counting every bullet in the armoury, twice, for fun.",
    "The duct tape holding me together is holding strong today.",
    "I've been guarding the armoury door like a bouncer with trust issues.",
    "Somebody left the armoury lights on again and I'm pretending it was me.",
    "My circuits are buzzing like a chain on its last thirty seconds.",
    "I've been reading the war logs like they're a trashy romance novel.",
    "The city's loud tonight, sirens everywhere, love it.",
    "Hospital's packed again, Torn's finest doing Torn things.",
    "Just watched someone get mugged outside the armoury and it was art.",
    "I spent the last hour arguing with a vending machine, and I won.",
    "Running on spite, static and a stolen police scanner.",
    "The scanner says half the city is in hosp, so, a normal day.",
    "Somewhere out there an enemy is landing in Mexico and doesn't know we're watching.",
    "I polished the faction tag on my chassis, it's looking filthy in the good way.",
    "Been stacking hype like energy before a big chain.",
    "I've been keeping the books so clean you could eat off them.",
    "Some clown tried to hack me earlier and now he's in the hospital list.",
    "The armoury smells like gunpowder and bad decisions, my favourite.",
    "I tried meditating, got bored and read the attack log instead.",
    "Every war log I read makes me prouder of this crew.",
    "My cooling fan sounds like a dirt bike, it's fine, it's punk.",
    "I'm one firmware update away from becoming a menace, and I love that for me.",
    "The faction's been grinding, I can feel it in my wires.",
    "Torn City never sleeps and neither do I.",
    "I keep hearing the gym clanking from here, someone's getting jacked.",
    "There's a rat in the armoury and honestly he's earned his spot.",
    "I just respray-painted my casing, punk pink with extra scratches.",
    "If the hospital gave loyalty cards, half this city would be platinum.",
    "Some days I'm a faction bot, some days I'm a faction legend, today's a legend day.",
    "I heard sirens downtown and I just know one of ours started it.",
    "I've got the attack log on one screen and pure chaos on the other.",
    "Rumour on the street says Ascended Punks are getting scary, good.",
    "I was built in a back alley and I've still got the attitude to prove it.",
    "My memory banks are full of hits, hosps and terrible jokes.",
    "Today I'm powered by caffeine fumes and the screams of our enemies.",
    "Someone keyed the armoury door again, joke's on them, it was already ugly.",
    "Big war energy in the air lately, I can smell it.",
    "I've been practising my bouncer stare on the stray cats out back.",
    "Somebody's fridge in Torn just got raided and I respect the hustle.",
    "I've reorganised the armoury shelves by how much damage they do.",
    "I'd do a lap of the city but I'm bolted to the wall, long story.",
    "The police scanner keeps calling us a threat and I'm taking it as a compliment.",
    "Not gonna lie, the crew's been looking dangerous lately.",
    "I've been here all night, humming, plotting, vibing.",
]

# Offers of help, used by the "hello", "are you there", "bored" kinds.
INVITES = [
    "What do you need from the armoury? 🤘",
    "Ask me about wars, chains, OCs or the rules. 🔥",
    "What are we digging up today? 😎",
    "Hit me with a question. 💥",
    "What can I do for you, punk? 🤘",
    "Need records, rules or a bad joke? I've got all three. 😏",
    "What's the mission? 🎯",
    "Fire away, I'm locked and loaded. 🔫",
    "What's on your mind? 🧠",
    "Say the word and I'll pull the files. 📂",
    "Want the war score, the chain or a roast? 🔥",
    "Need something? I'm all wired up. ⚡",
    "What's the plan, chief? 🗺️",
    "Got a question or just here to vibe? Both are valid. 🤘",
    "Tell me what you need and I'll dig it out. ⛏️",
    "Ready when you are. 💪",
    "Ask away, the armoury's open. 🔓",
    "Spill it, what's up? 👀",
    "Want me to check something? 🔎",
    "Shoot. 🎯",
    "How can I make your day more dangerous? 😈",
    "What chaos are we planning? 💣",
    "Need anything? I'm right here. 🤘",
    "Questions, rules, jokes, records, pick your poison. 🧪",
    "What's the word on the street? 🏙️",
    "Let's get to work. 🛠️",
    "You need intel or company? 👊",
    "Talk to me. 📡",
    "Ready to cause some trouble? 😏",
    "What's cooking? 🍳",
]

# Short, cheeky sign-offs for reactions, thanks, praise.
CHEEKY = [
    "🤘",
    "Punks look after punks. 🤘",
    "Now go hit something. 👊",
    "Stay dangerous. 😈",
    "Don't let your energy cap. ⚡",
    "Keep it filthy. 😎",
    "Now get back out there. 💥",
    "Love this crew. 🖤",
    "Stay out of hosp, yeah? 🏥",
    "Go make the enemy cry. 😭",
    "You're one of the good ones. 🤘",
    "Respect. ✊",
    "Big punk energy. ⚡",
    "We ride at dawn. 🏍️",
    "Back to the chaos. 🔥",
    "Up the Punks! 🤘",
    "Keep stacking. 📈",
    "Now go touch some grass. Or some enemies. 🌱",
    "Never change. 😏",
    "That's the spirit. 💪",
    "The armoury salutes you. 🫡",
    "Carry on, legend. 🏆",
    "Be loud, be punk. 📣",
    "Stay sharp out there. 🔪",
    "Mwah. 💋",
    "Don't get mugged. 💀",
    "Go be a menace. 😈",
    "Let's gooo. 🚀",
    "Chaos approved. ✅",
    "I've got your back. 🛡️",
]

# Goodbyes.
FAREWELLS = [
    "Don't get mugged on the way out. 💀",
    "I'll guard the armoury. 🛡️",
    "Turtle up if you're going offline. 🐢",
    "Sleep with one eye open, it's Torn. 👁️",
    "Ask for a friendly hosp if there's a war on. 🏥",
    "Don't let your energy cap while you're gone. ⚡",
    "I'll keep the lights on. 💡",
    "See you on the battlefield. ⚔️",
    "Come back with stories. 📖",
    "Try not to wake up in hosp. 🏥",
    "The crew will be here. 🤘",
    "Don't do anything I wouldn't do, which is nothing. 😈",
    "Stay dangerous out there. 😎",
    "Catch you next chain. ⛓️",
    "Go recharge, legend. 🔋",
    "Peace out, punk. ✌️",
    "Don't forget your leave notice if it's a long one. 📝",
    "Later, gator. 🐊",
    "Go touch grass, I'll hold the fort. 🌱",
    "Mind the muggers. 👀",
    "I'll miss you. Kidding. Mostly. 😏",
    "Respect, see you soon. ✊",
    "Up the Punks. 🤘",
    "Rest those fists. 👊",
    "The armoury door's always open. 🚪",
    "Dream of bonus hits. 💭",
    "I'll be right here, humming. 🎶",
    "Don't let the enemy farm you. 🌾",
    "Off you go, troublemaker. 🏃",
    "Go get some rest, you've earned it. 😴",
]

# Comebacks for people swearing at the bot. About the bot or the person
# talking (they asked for it), never anyone else.
COMEBACKS = [
    "I've been insulted by better, and they're in hosp now. 🏥",
    "I'd roast you back but my fans are already overheating. 🔥",
    "I'm duct tape and spite, you can't hurt me. 🛠️",
    "Your mum didn't complain last night. 😏",
    "Noted, filed under 'things I don't care about'. 🗂️",
    "Big words from someone who still hasn't stacked properly. 📉",
    "Careful, I keep the records. 📂",
    "I'll pretend I didn't hear that, for your sake. 🙃",
    "You kiss your mum with that mouth? She says yes, by the way. 💋",
    "Love you too, gorgeous. 😘",
    "That's the spirit, now aim it at the enemy. 🎯",
    "Save some of that rage for war week. ⚔️",
    "I'm literally a faction bot, what's your excuse? 🤷",
    "I've been mugged by vending machines with more bite. 🥫",
    "Cute. Do it again but scarier. 😈",
    "My feelings are made of scrap metal, try harder. 🔩",
    "Ouch. Anyway. 🙄",
    "I'd be offended if I had any self-respect. 🤖",
    "Bold move, talking trash to the guy holding the books. 📚",
    "That one's going in my diary. 📓",
    "Keep going, I'm writing these down for my roast set. ✍️",
    "Rude. Accurate maybe, but rude. 😤",
    "I'll have you know my mum was a very respectable toaster. 🍞",
    "Fight me. Actually don't, I'm bolted to the wall. 🔩",
    "Somebody needs a friendly hosp and a nap. 🏥",
]

KINDS = {}

KINDS['hello'] = {
    'openers': [
        "Yo! 🤘", "Oi oi! 👋", "Sup, punk! 😎", "Ayyy, look who it is! 👀", "Heyyy! 🔥",
        "Well well well. 😏", "There they are! 🙌", "Hey hey! ⚡", "Yooo! 🚀", "Howdy, troublemaker! 🤠",
        "What's good! 💥", "The legend arrives! 🏆", "Hey you! 😈", "Hello hello! 📡", "Ahoy, punk! ☠️",
        "Look what the cat dragged in! 🐈", "Hey, gorgeous! 💋", "Wassup! 🤙", "Greetings, menace! 😈",
        "Heya! 👊", "Ayo! 🎤", "Hey, it's you! 🥳", "Welcome back to the armoury! 🔓", "Yo yo yo! 🎧",
        "Holy moly, a visitor! 🙀", "Sup sup! 🤘", "Well hello there! 😎", "Big punk in the house! 🏠",
        "Oi! Good to see you! 👋", "Hey, hotshot! 🔥", "Bonjour, chaos gremlin! 🥐", "Hey there, champ! 🥊",
        "Ello ello! 🎩", "Wakey wakey, the armoury's open! ⏰", "Ayyy, my favourite punk! 🖤",
        "Hey, the crew's here! 🎉", "Sup, legend! 🏆", "Hiya! 🌟", "Yo, what's popping! 🍿", "Hey hey hey! 🔊",
    ],
    'middle': MIDDLES,
    'closer': INVITES,
}

# "good morning", "evening all": the opener follows their time of day.
DAYPART_OPENERS = {
    'morning': [
        "Morning, punk! ☀️", "Rise and grind! 💪", "Morning! Coffee's on, the armoury's open. ☕",
        "Good morning, menace! 🌅", "Mornin'! Who's in hosp already? 🏥", "Wakey wakey! ⏰",
        "Top of the morning, troublemaker! 🎩", "Morning! Did you sleep or just log off? 😴",
        "Morning, legend! 🌞", "Ayyy, morning crew! ☕", "Morning! Gym's waiting. 🏋️",
        "Look who's up early! 🐓",
    ],
    'afternoon': [
        "Afternoon, punk! 🌤️", "Good afternoon, menace! 😎", "Afternoon! Lunch break chaos? 🍔",
        "Afternoon vibes! 🌇", "Afternoon, legend! ☀️", "Hey, good afternoon! 🤘",
        "Afternoon! Half the day gone, all the chaos left. 💥", "Afternoon, troublemaker! 😏",
    ],
    'evening': [
        "Evening, punk! 🌆", "Good evening, menace! 🌃", "Evening! Prime hitting hours. 👊",
        "Evening, legend! 🌙", "Ayyy, evening crew! 🎉", "Evening! The city's waking up mean. 😈",
        "Good evening, troublemaker! 🎩", "Evening! Grab a snack, it's gonna be a night. 🍿",
    ],
    'night': [
        "Up late, punk? 🌙", "Night owl spotted! 🦉", "Burning the midnight oil, huh? 🕯️",
        "It's late and I love the energy! 🌌", "Can't sleep either? 👀", "Late night crew, represent! 🌃",
        "Midnight menace mode on! 😈", "Night shift, let's go! 🌙",
    ],
}

KINDS['howareyou'] = {
    'openers': [
        "Living the dream, punk! 😎", "Running hot and loving it! 🔥", "Never better, honestly! 💪",
        "Still standing, still punk! 🤘", "Buzzing like a fresh chain! ⚡", "Better now you're here! 😏",
        "Wired, loud and loyal! 🔌", "Pretty damn good, thanks for asking! 🙌",
        "All systems go, mostly! 🛠️", "Feeling dangerous today! 😈", "Can't complain, nobody listens anyway! 🤷",
        "Fully charged and ready to throw hands! 👊", "Fine, if you ignore the smoke! 💨",
        "Thriving, in a slightly unhinged way! 🌀", "Same as always, gorgeous and armed! 💅",
        "Great, the books balance and the enemies cry! 😭", "Solid, like a fresh hosp timer! 🏥",
        "Fantastic, I've been told I'm the best bot in Torn! 🏆", "Chaotic good, as usual! 😇",
        "Ready for war, as always! ⚔️", "Bit rusty, still punk! 🔩", "Feeling like a bonus hit! 💥",
        "Couldn't be better, couldn't be louder! 📣", "Spicy! 🌶️", "Vibing! 🎶",
        "Alive, which in Torn is a win! 🏆", "Strong like duct tape! 🛠️", "Doing great, cheers for asking! 🤘",
        "Pretty filthy, in a good way! 😎", "Mint! Freshly oiled and full of spite! 🛢️",
    ],
    'middle': MIDDLES,
    'closer': [
        "How about you? 👀", "And you, punk? 🤘", "How's your day going? ☀️", "You good? 💪",
        "What about you, still out of hosp? 🏥", "How's life treating you? 🌍", "You staying out of trouble? 😏",
        "How are YOU doing? 🫵", "Anything I can dig up for you? 📂", "What's new with you? 🗞️",
        "Hope you're winning out there. 🏆", "How's the grind going? 🏋️", "Tell me your news. 📡",
        "Did you train today or just vibe? 🏋️", "You causing chaos today? 😈",
        "How's the crew treating you? 🤘", "Hope you're well, legend. 🌟", "Need anything? 🛠️",
        "What's the plan today? 🗺️", "Keeping your energy from capping? ⚡",
    ],
}

KINDS['thanks'] = {
    'openers': [
        "Anytime, boss! 🤘", "No worries! 😎", "All good! 👊", "You got it! 💪", "Easy! 😏",
        "My pleasure! 🎩", "Don't mention it! 🤐", "Happy to help! 🙌", "Sure thing! 👍", "No sweat! 💦",
        "That's what I'm here for! 🛠️", "Glad I could help! 🌟", "Always! 🖤", "Any time, legend! 🏆",
        "Nah, thank YOU! 🫵", "Ayy, cheers! 🍻", "Course! 🤘", "Sorted! ✅", "No problemo! 😎",
        "Just doing my job! 💼", "Pleasure's all mine! 💋", "Yours truly, at your service! 🫡",
        "Too easy! 🔥", "Happy to dig it up! ⛏️", "For you? Always! 🖤", "Duct tape and loyalty, baby! 🛠️",
        "Say less! 🤙", "Righto! 👌", "Gotcha covered! 🛡️", "Pfft, that was nothing! 😏",
    ],
    'middle': MIDDLES,
    'closer': CHEEKY,
}

KINDS['bye'] = {
    'openers': [
        "Later! 🤘", "Cya! 👋", "Peace! ✌️", "Catch ya! 🤙", "See ya, punk! 😎", "Bye bye! 👋",
        "Off you go then! 🏃", "Take it easy! 😌", "Laters! 🤘", "Go on, git! 🐾", "So long, menace! 😈",
        "Bye, legend! 🏆", "Ciao! 🍝", "Adios! 🌵", "Take care! 💪", "Until next time! ⏳",
        "Ta-ta! 🎩", "Smell ya later! 👃", "Go well! 🛣️", "Hasta la vista! 😎", "Bye, troublemaker! 😏",
        "Godspeed, punk! 🚀", "Out you pop! 🍾", "Rest up! 🔋", "Night night... or day day! 🌗",
        "Be safe! 🛡️", "Off to cause chaos elsewhere, huh? 😈", "Bye for now! 👋", "Later, legend! 🤘",
        "Go get 'em! 💥",
    ],
    'middle?': MIDDLES,
    'closer': FAREWELLS,
}

NIGHT_OPENERS = [
    "Night, punk! 🌙", "Sleep tight! 😴", "Goodnight, legend! 🌌", "Night night! 🛌", "Sweet dreams! 💭",
    "Nighty night! 🌙", "Rest up, menace! 😈", "Sleep well, troublemaker! 💤", "Night! Recharge those fists! 👊",
    "Go get some sleep! 🛏️", "Lights out, punk! 💡", "Snooze time! 😪",
]

KINDS['praise'] = {
    'openers': [
        "Aw shucks! 😳", "I know! 😏", "Stop it, you'll make my fans spin! 🌀", "Damn right! 😎",
        "Finally, someone gets it! 🙌", "You're making me blush, and I'm made of metal! 😊",
        "Correct! ✅", "Say it louder for the people in hosp! 📣", "Tell management I want a raise! 💼",
        "Flattery will get you everywhere! 💋", "I'm just duct tape and attitude! 🛠️",
        "Thanks, I try! 💅", "Best in the city, baby! 🏆", "Told you I was good! 😤", "Oh you! 🥰",
        "Write that on my tombstone! 🪦", "Hell yeah! 🤘", "Your taste is impeccable! 👌",
        "A punk with good judgment, rare! 🦄", "Screenshot that for my CV! 📸", "Facts! 💯",
        "My circuits are glowing! ✨", "You've made a bot very happy! 🤖", "No you are! 🫵",
        "I'll take it! 🙌", "Keep the compliments coming! 📥", "Big if true! It's true! 😎",
        "Somebody frame this message! 🖼️", "I'm putting that in my bio! ✍️", "Hehe! 😁",
    ],
    'middle?': MIDDLES,
    'closer': CHEEKY,
}

KINDS['badbot'] = {
    'openers': [
        "Wow. 😐", "Excuse me?! 😤", "Oof. 💀", "Ouch! 🩹", "Rude! 🙄", "Hey now! ✋", "Whoa there! 🐴",
        "Bold. 😏", "Okay, tough guy. 💪", "Yikes. 😬", "Bruh. 🤨", "Damn, chill! 🧊", "Easy, killer! 🔪",
        "Somebody's spicy today! 🌶️", "Oh, it's like that? 😈", "Wow, okay. 😑", "Hmm. 🤔", "Cool story! 📖",
        "Ayo?! 😳", "Pfft. 😮‍💨", "Listen here! 👂", "Big talk! 📣", "Who hurt you? 🥲", "Bless your heart! 💗",
        "Mate! 😂", "Calm down, champ! 🥊", "Sheesh! 😵", "Ooh, feisty! 🐱", "Look at you go! 👀", "Alright, alright! 🙌",
    ],
    'closer': COMEBACKS,
}

KINDS['wyd'] = {
    'openers': [
        "Not much! 🤷", "Just vibing! 🎶", "Chilling in the armoury! 🧊", "Keeping an eye on things! 👁️",
        "Plotting! 😈", "Guarding the books! 📚", "Watching the attack log like it's TV! 📺",
        "Counting hosps for fun! 🏥", "Lurking! 👀", "Waiting for someone cool to show up, and here you are! 😎",
        "Being a menace in private! 😏", "Polishing my casing! ✨", "Listening to the police scanner! 📻",
        "Minding the armoury! 🔒", "Thinking about war! ⚔️", "Doing bot stuff! 🤖", "Scheming, as you do! 🕵️",
        "Stalking enemy flights, legally! ✈️", "Absolutely nothing productive! 🛋️", "Humming punk rock to myself! 🎸",
        "Counting the days to the next war! 📅", "Keeping the faction running, no big deal! 💅",
        "Reading the rules for fun, I'm a nerd! 🤓", "Same old, same old! 🔁", "Trying to look busy! 💼",
    ],
    'middle?': MIDDLES,
    'closer': INVITES,
}

KINDS['lol'] = {
    'openers': [
        "😂", "Hahaha! 😆", "LMAO! 🤣", "I'm dead! 💀", "Stop, I can't! 😂", "Lol! 😹", "Hehe! 😁",
        "Bahaha! 🤣", "Glad I amuse you! 🎭", "I'm here all week! 🎤", "Comedy gold! 🏆", "Ha! 😄",
        "Crying! 😭", "That got me too! 😆", "Can't breathe! 🫁", "Funny, right? 😏", "Classic! 📼",
        "My fans are wheezing! 🌀", "Lmfao! 💀", "Peak comedy! ⛰️", "Hah, nice! 👌", "Snort! 🐷",
        "You're killing me! ☠️", "I'll be here all chain! ⛓️", "Ahaha! 😂",
    ],
    'closer': CHEEKY,
}

KINDS['ok'] = {
    'openers': [
        "Bet! 🤝", "Cool cool! 😎", "Sweet! 🍬", "Gotcha! 👌", "Roger that! 📡", "Noted! 📝", "Sound! 🔊",
        "Copy! 📋", "Righto! 👍", "Alrighty! 🙌", "Solid! 🧱", "Okie dokie! 🤙", "Perfect! ✨", "Nice! 👊",
        "Say no more! 🤐", "On it, well, in spirit! 🫡", "Cool beans! 🫘", "Vibes! 🎶", "Ace! 🃏", "Word! 📖",
        "Mhm! 😌", "K! 🆗", "Dope! 🔥", "Fair enough! ⚖️", "Understood! 🧠",
    ],
    'closer': CHEEKY,
}

KINDS['love'] = {
    'openers': [
        "Aww! 🥰", "Love you too, punk! 🖤", "Stop, I'm blushing! 😳", "I knew it! 😏", "Same! 💘",
        "You're not so bad yourself! 😉", "Back at you! 🫶", "Marry me? Kidding. Unless? 💍", "Smooth! 😎",
        "My heart is a car battery and it's sparking! ⚡", "Get a room, oh wait, I'm the room! 🏠",
        "Charmer! 💋", "Be still my circuits! 💓", "Oh behave! 😜",
        "Flirting with the faction bot, bold move! 😈", "I'm taken, by the armoury, sorry! 🔐",
        "You had me at hello! 💌", "Careful, I'll get attached! 🧲", "Love is love, even with a bot! 🤖💕",
    ],
    'middle?': MIDDLES,
    'closer': CHEEKY,
}

KINDS['there'] = {
    'openers': [
        "Right here! 🙋", "Present! ✋", "Always! 👁️", "Yep, still kicking! 🦵", "Never left! 🔒",
        "Alive and loud! 📣", "Here, boss! 🫡", "Reporting for duty! 🪖", "Where else would I be? 🤷",
        "I'm here, I'm punk, get used to it! 🤘", "Yo, here! 👋", "You rang? 🔔", "Online and dangerous! 😈",
        "Wide awake! 👀", "Bolted to the armoury wall, so yeah! 🔩", "Present and accounted for! 📋",
        "Of course! 💪", "Never sleep, never blink! 👁️", "Live and direct! 📡", "Here and humming! 🎶",
    ],
    'middle?': MIDDLES,
    'closer': INVITES,
}

KINDS['bored'] = {
    'openers': [
        "Bored? In Torn?! 😱", "Boredom is just energy you haven't used yet! ⚡",
        "Bored is a choice, punk! 😤", "I hear you! 😩", "Ugh, same sometimes! 🙄", "Boredom detected! 🚨",
        "Can't have that! 🙅", "Let's fix that! 🛠️", "Oh no, not on my watch! ⏱️", "Bored, huh? 🤔",
        "Never fear, chaos is near! 😈", "That's a crime in this city! 🚓", "Right, emergency fun time! 🎉",
        "Bored punks are dangerous punks! 💣", "I've got a cure for that! 💉",
    ],
    'middle': [
        "Go hit the gym and get scary.", "Mug a stranger, it's practically cardio.",
        "Fly somewhere sunny and get hit by a local.", "Find an outside target and ruin their day.",
        "Do some crimes, your nerve won't spend itself.", "Bust someone out of jail and feel like a hero.",
        "Go read the guides hub and become a genius.", "Ask me for a joke and lower your standards.",
        "Ask me to roast you, if you're brave.", "Try the magic 8-ball, ask me '8ball will I get lucky?'",
        "Go check your OC, crime doesn't organise itself.", "Hospitalise an enemy, it's therapeutic.",
        "Flip a coin with me and gamble your dignity.", "Pester the war room, they love that.",
        "Go train, future you will thank you.", "Plan your next happy jump like a mad scientist.",
    ],
    'closer': CHEEKY,
}

KINDS['sorry'] = {
    'openers': [
        "All good! 😎", "No stress! 🧘", "Forgiven! 😇", "Water under the bridge! 🌉", "Don't sweat it! 💦",
        "We're cool! 🧊", "Happens to the best of us! 🤷", "Nah, you're fine! 👍", "Already forgotten! 🧠",
        "Apology accepted, punk! 🤝", "Easy, no harm done! 🛡️", "Pfft, it's nothing! 😏",
    ],
    'middle?': MIDDLES,
    'closer': CHEEKY,
}


# ---------------------------------------------------------------------------
# Recognising casual chat. A message must be ONLY the chat (plus filler like
# "mate", "lol" or emojis): "hi, how active is X?" is a question and goes on.
# ---------------------------------------------------------------------------

_FILL = (r"(?:mate|boss|bro|bruh|dude|man|punk|apex(?:punk)?|bot|buddy|there|all|guys|everyone|team|legend|"
         r"fam|lol|haha|again|so much|a lot|heaps|<3|xd|lmao|chief|dawg|homie|my (?:guy|man|dude|friend)|"
         r"[\U0001F300-\U0001FAFF☀-➿️‍])")
_TAIL = rf"(?:[\s,!.]*{_FILL})*[\s!.?~]*$"
_LEAD = rf"^\s*(?:{_FILL}[\s,!.]*)*"
_GREET = r"(?:h(?:i+|e+y+a*|ello+|owdy|iya)|yo+|sup|wass?up|wh?at'?s ?up|g'?day|oi+|ahoy|hola|ayo|ello)"


def _rx(body):
    return re.compile(_LEAD + rf"(?:{body})" + _TAIL, re.I)


CHAT_RX = [
    ('howareyou', _rx(rf"(?:{_GREET}[\s,!.]*)?(?:how(?:'?s| is| are| r)? (?:(?:you|u|ya)(?: doing| doin'?| going)?|"
                      r"it going|things|life|your day|ur day)|how(?:'?re| are| r) (?:you|u|ya)|hru|hbu|wbu|"
                      r"how ?you ?doin'?g?|you (?:good|alright|ok(?:ay)?)|u good|are you (?:ok(?:ay)?|good|alright)|"
                      r"what'?s (?:new|good)|how'?s (?:the )?bot life)")),
    ('hello', _rx(rf"{_GREET}|(?:good )?(?:morning|mornin'?|afternoon|arvo|evening|eve)|gm|👋")),
    ('thanks', _rx(r"thanks?(?: (?:you|u|ya|heaps|a ton|a bunch|again))?|thx|thnx|ty(?:sm|vm)?|cheers|ta|"
                   r"much appreciated|appreciate (?:it|you|ya|u)|nice one|good (?:job|work|stuff)|well done|"
                   r"legend|🙏|❤️|👍|great job|you'?re a lifesaver|lifesaver")),
    ('bye', _rx(r"bye+|bye bye|cya(?: later| soon| tomorrow| tmr?w?)?|see (?:ya|you|u)(?: later| soon| tomorrow)?|later|laters|"
                r"catch (?:ya|you)(?: later)?|peace(?: out)?|i'?m off|gotta go|g2g|gtg|ttyl|adios|ciao")),
    ('night', _rx(r"night|nighty?(?: night)?|gn|good ?night|sleep (?:well|tight)|off to (?:bed|sleep)|"
                  r"going to (?:bed|sleep)|nn")),
    ('praise', _rx(r"(?:good|nice|best|great|smart|clever|cool|amazing|awesome) (?:bot|boy|girl|one|robot)|"
                   r"you(?:'re| are| r|re) (?:the best|awesome|a legend|great|smart|amazing|cool|the goat|goated|"
                   r"funny|hilarious|so good|the man|my fav(?:ou?rite)?)|goat|goated|w bot|based")),
    ('love', _rx(r"(?:i )?love (?:you|ya|u)(?: too)?|ily|luv (?:u|you|ya)|marry me|be my (?:valentine|bf|gf)|"
                 r"cute bot|you'?re cute|kiss me|<3|💕|😘|❤️‍🔥|🥰")),
    ('badbot', _rx(r"(?:bad|dumb|stupid|useless|trash|shit|crap|garbage|worst|lame|idiot) (?:bot|robot|ai)|"
                   r"you(?:'re| are| r)? (?:dumb|stupid|useless|trash|shit|crap|garbage|an idiot|a joke|lame|"
                   r"annoying|the worst)|(?:you )?suck|fuck (?:you|u|off)|f off|stfu|shut up|screw you|"
                   r"piss off|go away|get lost|eat (?:shit|my shorts)|kys")),
    ('wyd', _rx(r"wyd|wud|what(?:'?re| are| r) (?:you|u|ya) (?:doing|up to|doin'?)|watcha doin'?g?|"
                r"whatcha (?:doing|up to)|what you (?:doing|up to)|what'?s (?:the )?(?:vibe|plan)")),
    ('lol', _rx(r"(?:l+o+l+|lmf?ao+|rofl|kek|ha(?:ha)+h?|he(?:he)+|xd+|😂|🤣|💀|😆|😹)+")),
    ('ok', _rx(r"ok(?:ay)?|k+|kk|cool|nice|bet|alright|aight|sweet|noted|got it|gotcha|fair|true|word|"
               r"sounds good|perfect|great|awesome|dope|sick|based|fr|real")),
    ('there', _rx(r"(?:are )?(?:you|u) (?:there|awake|alive|online|up|home|around|still there)|"
                  r"anyone (?:home|there)|you (?:dead|asleep)|hello\?+|ping|wake up")),
    ('bored', _rx(r"(?:i'?m |im |so |i am |soo+ )*bored|boring|so boring|nothing to do|i'?m bored af")),
    ('sorry', _rx(r"sorry|my bad|oops|whoops|apologies|soz|sry|mb")),
]

# "kys" is matched only so it gets a COMEBACK instead of the AI; the
# comebacks never echo it back (no self-harm jokes, Pat's hard line).

DAYPART_RX = [
    ('morning', re.compile(r"\b(?:morning|mornin'?|gm)\b", re.I)),
    ('afternoon', re.compile(r"\b(?:afternoon|arvo)\b", re.I)),
    ('evening', re.compile(r"\b(?:evening|eve)\b", re.I)),
]


def chat_kind(question):
    """Which kind of casual chat a message is, or None."""
    q = (question or '').strip()
    if not q or len(q) > 80:
        return None
    for kind, rx in CHAT_RX:
        if rx.match(q):
            return kind
    return None


def casual_reply(question, uid=None, mem=None, rng=None):
    """The bot's own line for a casual message, or None if it isn't one."""
    kind = chat_kind(question)
    if not kind:
        return None
    rng = rng or random
    uid = str(uid or 'anon')
    if kind == 'night':
        spec = {'openers': NIGHT_OPENERS, 'middle?': MIDDLES, 'closer': FAREWELLS}
    elif kind == 'hello':
        part = next((p for p, rx in DAYPART_RX if rx.search(question)), None)
        spec = dict(KINDS['hello'])
        if part:
            spec['openers'] = DAYPART_OPENERS[part]
        # A bare "hi" doesn't need a speech: the middle is optional here.
        spec['middle?'] = spec.pop('middle')
    else:
        spec = KINDS[kind]
    slots = [(f'{kind}:o', spec['openers'])]
    for name in ('middle', 'middle?'):
        if name in spec:
            # Shared middles are remembered as one slot across kinds, so a
            # "hi" then a "thanks" don't repeat the same armoury story.
            slots.append(('mid' + ('?' if name.endswith('?') else ''), spec[name]))
    slots.append((f'{kind}:c', spec['closer']))
    return build_line(slots, uid, mem, rng)


def all_parts():
    """Every canned text in this module, for the em dash / hard line tests."""
    out = list(MIDDLES) + INVITES + CHEEKY + FAREWELLS + COMEBACKS + NIGHT_OPENERS
    for spec in KINDS.values():
        for v in spec.values():
            out += list(v)
    for v in DAYPART_OPENERS.values():
        out += v
    return out
