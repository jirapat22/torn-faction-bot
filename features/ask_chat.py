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
MEMORY_LINES = 1000         # full lines remembered per person (500 chats once repeated one at 400)
RECENT_PARTS = 20           # parts per slot a person won't see again soon
# Jokes and roasts are the punchline itself: a person should hear every one
# before any comes back, so these slots remember far more.
SLOT_KEEP = {'joke': 150, 'roast': 120, 'self': 40, 'ball': 30}


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
            del lst[:-SLOT_KEEP.get(slot, RECENT_PARTS)]
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
# Thai too (the crew has Thai speakers): sawasdee / สวัสดี / หวัดดี.
_GREET = (r"(?:h(?:i+|e+y+a*|ello+|owdy|iya)|yo+|sup|wass?up|wh?at'?s ?up|g'?day|oi+|ahoy|hola|ayo|ello|"
          r"sa+wa+s?dee+(?: (?:krub|khrap|krap|ka|kha|na))?|สวัสดี\S*|หวัดดี\S*|ดีจ้า|ดีครับ|ดีค่ะ)")


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
                   r"legend|🙏|❤️|👍|great job|you'?re a lifesaver|lifesaver|"
                   r"kh?o+[bp] ?(?:kh?|c)u+n(?: (?:krub|khrap|krap|ka|kha|mak))*|ขอบคุณ\S*|ขอบใจ\S*|แต๊งกิ้ว\S*")),
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
    question = strip_join(question)
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


# ---------------------------------------------------------------------------
# About the bot (Pat, 23/11/2026). Ikaros22 is Pat's Torn name.
# ---------------------------------------------------------------------------

CREATOR = 'Ikaros22'

ABOUT = {
    'who': {
        'rx': r"who (?:are|r) (?:you|u|ya)|what (?:are|r) (?:you|u)|introduce yourself|what'?s your name|"
              r"what is your name|who is this|who'?s this|tell me about (?:yourself|you)|who is apex ?punk",
        'openers': ["Who am I? 😏", "Glad you asked! 🎤", "Ayy, the intro! 🤘", "Allow me to introduce myself! 🎩",
                    "Oh, you don't know me yet? 👀", "The one and only! 🏆", "Story time! 📖", "Pull up a crate! 📦"],
        'core': [
            "I'm ApexPunk, the Ascended Punks faction bot, hot-wired together in a Torn back alley from a busted "
            "vending machine, a stolen police scanner and a lot of duct tape.",
            "I'm ApexPunk, the faction's bot: I keep the books, watch wars, chains, flights and OCs, and answer "
            "questions about all of it.",
            "I'm ApexPunk. I live in the faction armoury, keep the records and watch the enemy so you don't have to.",
            "I'm the Ascended Punks' own bot, ApexPunk: part record keeper, part lookout, part bad comedian.",
        ],
        'closer': INVITES,
    },
    'ai': {
        'rx': r"(?:are|r) (?:you|u) (?:an? )?(?:ai|bot|robot|real|human|person|a real person|chat ?gpt|gpt|"
              r"claude|gemini)|(?:is this|are you) (?:an? )?(?:ai|bot)|"
              r"what (?:ai|model|llm) (?:are|do) (?:you|u)(?: use)?",
        'openers': ["Busted! 🤖", "Beep boop, yes. 🤖", "Real? I'm realer than your gym routine. 💪",
                    "Human? Please, I'm way better looking. 💅", "Good question! 🧠", "Honest answer? 🫡"],
        'core': [
            "I'm a bot, made by Ikaros22. Records answers come straight from the faction's own files, and for "
            "chatty or tricky questions I borrow a brain from an AI service.",
            "I'm a bot built by Ikaros22. The numbers I give come from the bot's records, never made up, and an "
            "AI only helps me word the trickier answers.",
            "Not human, not quite alive: a bot Ikaros22 built. The records are real, the attitude is extra.",
        ],
        'closer': CHEEKY,
    },
    'maker': {
        'rx': r"who (?:made|built|created|coded|wrote|programmed|owns|runs) (?:you|u|this bot|the bot)|"
              r"who(?:'?s| is) (?:your|ur) (?:creator|maker|dev|developer|owner|daddy|mum|mom|dad|boss)|"
              r"who do you (?:work for|belong to)",
        'openers': ["That'd be the legend, Ikaros22. 👑", "Credit where it's due! 🏆", "My maker? 🛠️",
                    "Ah, the mad scientist! 🧪", "Respect the creator! 🙏", "Who else? 😎"],
        'core': [
            "Ikaros22 built me, wired me up and taught me everything I know.",
            "Ikaros22 made me. Blame Ikaros22 for the jokes.",
            "I was built by Ikaros22, one bolt and one bad decision at a time.",
            "Ikaros22 is my creator. I'm basically Ikaros22's most loyal, loudest project.",
        ],
        'closer': CHEEKY,
    },
    'alive': {
        'rx': r"(?:are|r) (?:you|u) (?:still )?(?:alive|ok|okay|broken|working|sentient|conscious)|do you (?:sleep|dream|eat|feel)|"
              r"do (?:you|u) have (?:feelings|a life)|how old are (?:you|u)|where do (?:you|u) live",
        'openers': ["Alive enough to roast you! 😈", "Define alive. 🤔", "Still kicking! 🦵",
                    "Fully operational! ⚙️", "Ha! 😏", "Deep question, punk. 🌌"],
        'core': [
            "I live in the faction armoury, I never sleep, and I run on duct tape and loyalty.",
            "I don't sleep, I don't eat, I just watch the attack log and plot.",
            "I'm a bot, so no feelings, but I'd still take a bullet for this crew. Metaphorically. I'm bolted to the wall.",
            "Born in a back alley, raised by the Punks, living in the armoury ever since.",
        ],
        'closer': CHEEKY,
    },
}

for _spec in ABOUT.values():
    _spec['rx'] = _rx(rf"(?:{_GREET}[\s,!.]*)?(?:so[\s,]+|but[\s,]+|wait[\s,]+)?(?:{_spec['rx']})")


def about_reply(question, uid=None, mem=None, rng=None, help_url=None):
    """Who/what the bot is, who made it, what it can do. None if not that."""
    q = strip_join(question).strip()
    if len(q) > 90:
        return None
    rng = rng or random
    uid = str(uid or 'anon')
    if WHAT_CAN_RX.match(q):
        where = f'the guide here: {help_url}' if help_url else 'the guide (type `@ApexPunk help`)'
        core = rng.choice([
            f"Everything I can do is in {where}",
            f"Check {where}, it's got the full list.",
            f"The full menu is in {where}",
        ])
        opener = pick_part(["Loads! 💪", "Oh, plenty! 😎", "What CAN'T I do? 😏", "Glad you asked! 🎤",
                            "Buckle up! 🏍️"], 'can:o', uid, mem, rng)
        line = f'{opener} {core} Records, wars, chains, OCs, flights, rules, jokes and roasts. 🤘'
        if mem:
            mem.remember(uid, {'can:o': opener}, line)
        return line
    for kind, spec in ABOUT.items():
        if spec['rx'].match(q):
            return build_line([(f'about:{kind}:o', spec['openers']), (f'about:{kind}', spec['core']),
                               (f'about:{kind}:c', spec['closer'])], uid, mem, rng)
    return None


WHAT_CAN_RX = _rx(r"what (?:else )?can (?:you|u) do|what (?:are|r) (?:you|u) (?:for|good for)|"
                  r"what do (?:you|u) know|what can i ask(?: you)?(?: about)?|what (?:else )?do (?:you|u) do|"
                  r"how do i use (?:you|u|this bot)|what are your (?:commands|features)")


# ---------------------------------------------------------------------------
# Fun: jokes, roasts, coin, 8-ball, dice, numbers, rate my X, pick X or Y.
# ---------------------------------------------------------------------------

JOKE_INTROS = ["Alright, alright. 🎤", "Here's one. 😏", "You asked for it. 😈", "Fresh from the armoury. 📦",
               "Okay, try not to hosp yourself laughing. 🏥", "Drumroll please. 🥁", "Hot off the scanner. 📻",
               "This one's a banger. 💥", "Get a load of this. 👀", "Brace yourself. 🫣", "Sure thing. 🤘",
               "One joke, coming up. 🍽️", "Ahem. 🎩", "Listen up, punk. 👂", "I've been saving this one. 💾",
               "Don't blame me, blame Ikaros22. 🛠️", "Okay, here goes. 🎬", "Comedy hour, armoury edition. 🎭",
               "Joke incoming. 🚀", "Classic Torn humour. 🏙️"]

JOKES = [
    "Why did the Torn player bring a ladder to the fight? He heard the enemy's stats were through the roof. 🪜",
    "My hospital timer and my love life have one thing in common: both say 'come back later'. 🏥",
    "Why don't enemies play hide and seek with us? Because hiding in hosp only works for so long. 🙈",
    "What's a mugger's favourite exercise? Running off with your stuff. 🏃",
    "I asked the hospital for a discount. They said I'm already their best customer. 🏥",
    "Why did the chain break? It didn't have enough support. Neither do my jokes. ⛓️",
    "What do you call a Torn player who never trains? A punching bag with a username. 🥊",
    "I told my enemy I'd see him in hosp. He said 'you first'. He was right. 🚑",
    "Why are bonus hits like birthday cake? Everyone wants one and someone always takes it early. 🎂",
    "What's the difference between a level holder and a trap? Nothing, ask anyone who attacked one. 🪤",
    "Why did the punk fly to Mexico? To get hospitalised somewhere sunny. 🌮",
    "My energy bar and my motivation have the same problem: they cap and I do nothing about it. ⚡",
    "Why did the bot cross the road? To get away from the person asking for another joke. 🤖",
    "How does a Torn player say goodbye? 'See you in hosp.' 👋",
    "What's a jail's favourite music? Anything with a lot of bars. 🎵",
    "Why don't we tell secrets in faction chat? Too many leaks, and the plumber's in jail. 🔧",
    "What do you call a war with no hits? A very long nap. 😴",
    "My gym coach told me to lift heavy. So I lifted an enemy's stuff. Coach was not impressed. 🏋️",
    "Why did the chain watcher bring a stopwatch to bed? Habit. ⏱️",
    "How many punks does it take to change a light bulb? None, we hospitalise the dark. 💡",
    "Why do enemies fly to Cayman? To hide. Why do they come back? Because we're patient. 🏝️",
    "Turtling: the only time hiding in hospital counts as a strategy. 🐢",
    "My OC failed so badly the police sent a thank you card. 🚓",
    "I'm not saying the enemy is weak, but their hosp time is longer than their fight time. ⌛",
    "What do you call a punk who forgets to post availability? A strike waiting to happen. Kidding. Mostly. 📅",
    "What's an enemy's favourite country? Whichever one we're not in. 🗺️",
    "Why did the attacker bring a map? He kept getting lost in the hospital. 🗺️",
    "The best way to make a Torn player cry? Show them their energy capped while they slept. 😭",
    "Why did the bot get a job in the armoury? Because it was already bolted to the wall. 🔩",
    "Knock knock. Who's there? Leave. Leave who? Leave, not mug, it's war week. 🚪",
    "What's a chain's favourite dance? The hit and run. 💃",
    "Why do punks make good cooks? They know how to beat things. 🍳",
    "How do you know someone's been in Torn too long? They check hospital times before checking the weather. 🌦️",
    "Why was the enemy faction always cold? Too many hits from the Punks' wind-up. 🌬️",
    "My doctor said I need more rest. Joke's on him, I've been in his hospital all week. 🛌",
    "Why don't Torn players trust stairs? They're always up to something. 🪜",
    "What do you call a mugger in the rain? Wet, and still faster than you. 🌧️",
    "Why did the faction bot fail the driving test? It kept trying to chain the cars. 🚗",
    "If hospital beds had frequent flyer miles, I'd own an airline by now. ✈️",
    "Why do ranked wars never get boring? Because someone always forgets to turtle. 🐢",
    "Why did the scout bring binoculars to war? To spot the level holders before they spotted him. 🔭",
    "What's a Torn player's love language? A friendly hosp before logging off. 💕",
    "Why did the punk get kicked out of the library? Too many attacks on the silence. 📚",
    "I tried to start a peaceful faction once. We lasted four minutes. 🕊️",
    "My last fight was so short the hospital didn't even get my name. 🏥",
    "What's the scariest word in Torn? 'Landed'. Ask anyone who flew home mid-war. 🛬",
    "Why don't enemies chat in global after a war? Hard to type from a hospital bed. ⌨️",
    "My crimes success rate is like my dating life: mostly jail. ⛓️",
    "What do you call an enemy who flew abroad to hide? Delivery. 📦",
    "Why do punks love Mondays? Fresh week, fresh victims. 📅",
    "What's a bonus hit's least favourite word? 'Mine'. 🙅",
    "I'm not lazy, I'm stacking energy. That's my story and I'm sticking to it. 🔋",
    "What's the fastest way to make friends in Torn? Hospitalise their enemies. 🤝",
    "Why did the bot refuse to fight? It's a lover, not a fighter. Also it's bolted to the wall. 🔩",
    "What do you call a war week with no casualties? A dream I had once. 💭",
    "Why do Torn players hate elevators? They've had enough ups and downs in the hospital. 🛗",
    "What's a turtle's war plan? Hide, hosp, repeat. 🐢",
    "My combat strategy is simple: hit first, apologise never. 👊",
    "What did the enemy say after flying home? 'Why is there a welcome party in my hospital room?' 🎉",
]

JOKE_OUTROS = ["Ba dum tss. 🥁", "I'll be here all chain. ⛓️", "You're welcome. 😎", "Tough crowd. 😅",
               "Don't hosp me for that one. 🏥", "Thank you, thank you. 🙇", "Ikaros22 made me say it. 🛠️",
               "I'm wasted in this armoury. 🎭", "Laugh, it's an order. 🫡", "Hah! 😂", "Comedy gold. 🏆",
               "Okay that one was rough. 💀", "*mic drop* 🎤", "Want another? Just ask. 🔁", "Tip your bot. 😏",
               "Peak armoury humour. ⛰️", "Somebody laugh, please. 🦗", "I crack myself up. 🤣",
               "Rate it out of ten, I dare you. 🔟", "I'll show myself out. 🚪"]

ROAST_INTROS = ["You asked for this. 🔥", "Oh, it's roast time? 😈", "Alright, no mercy. 💀",
                "Sit down, this'll sting. 🪑", "Volunteer spotted! 🎯", "Bold of you. 😏", "Buckle up. 🏍️",
                "Grab some ice. 🧊", "Let's go. 🔥", "Don't cry, okay? 🥲", "You sure? Too late. 😈",
                "Roast mode: ON. 🔛", "Hope you've got a friendly hosp lined up. 🏥", "Fine, you earned it. 🏆",
                "Calling the hospital in advance. 🚑", "Ooh, a brave one. 🦁", "Here comes the heat. 🌶️",
                "Light 'em up. 🔥", "Get comfy. 🛋️", "Stand still, this won't take long. 🎯"]

ROASTS = [
    "You're so soft, enemies use you as a friendly hosp.",
    "Your attack log reads like a cry for help.",
    "You've spent so long in hospital the nurses named a bed after you.",
    "Your gym sessions are so light the weights file noise complaints about the silence.",
    "Even the muggers feel sorry for you and leave a get-well card.",
    "You fly abroad so much the airline thinks you're a pilot. You still land in hosp.",
    "Your energy caps more often than you log in.",
    "You've got the fighting spirit of a wet napkin.",
    "Enemies don't scout you, they just see your name and relax.",
    "You make level holders look honest.",
    "Your idea of turtling is lying down in the street and hoping.",
    "You've been hospitalised by people who were offline.",
    "If losing was a stat, you'd be the strongest player in Torn.",
    "You chain like a toddler with a stopwatch.",
    "Your OC role should be called 'the distraction'.",
    "You're the reason the hospital has a loyalty programme.",
    "You hit like a sneeze in a hurricane.",
    "Your mum called. She wants her weak punches back.",
    "You've been mugged so often the muggers have you on a schedule.",
    "Even the jail cats won't fight you, it's beneath them.",
    "You train so little the gym sends you missing person posters.",
    "You call it 'stacking energy', we call it 'forgetting to log in'.",
    "Your war plan is basically 'get hit and hope they get bored'.",
    "The enemy faction keeps you on their list as a free point.",
    "Your bonus hit timing is so bad you'd take the hit on a ten and call it fifty.",
    "You're so slow the chain timer expired while you loaded the page.",
    "You're the human version of a 404 error.",
    "You've got main character energy and side quest damage.",
    "Your crimes fail so hard the police use them for training videos.",
    "Even your shadow has better stats than you, and it leaves when the lights go off.",
    "You're not a level holder, you're just low level.",
    "You walk into a fight like it's a spa day, and leave in an ambulance.",
    "If brains were energy, you'd be capped at zero.",
    "You're the reason the rulebook has so many bold bits.",
    "Your best fight was the one you lost slowly.",
    "You'd lose a staring contest to a hospital ceiling, and you've had practice.",
    "The only thing you've ever chained is excuses.",
    "Your mum's the only one who's ever hit harder than you, and she was trying to hug you.",
    "You get farmed so often the enemy calls you 'the harvest'.",
    "When you fly, the enemy faction throws a welcome party at the airport.",
    "Your attacks are so polite they should come with a thank you card.",
    "You'd get mugged in an empty room.",
    "Honestly, the vending machine I was built from had more fight in it.",
    "You've been on the losing side so long you've got a reserved parking spot there.",
    "You call yourself a punk but you've got the attitude of a decaf latte.",
]

ROAST_OUTROS = ["Love you though. 🖤", "Still my favourite punk. 🤘", "Now go prove me wrong. 💪",
                "Ice that burn. 🧊", "No refunds. 🚫", "Want an ambulance? 🚑", "Get wrecked. 💀",
                "Nothing personal. Okay, a bit personal. 😏", "Respect for asking though. ✊",
                "You'll bounce back. Probably from a hospital bed. 🏥", "Still crew. 🤝",
                "Go train, it helps. 🏋️", "That's love, punk style. 🖤", "Heal up. 🩹", "Cry about it. 😭",
                "Same time tomorrow? 😈", "Ikaros22 says sorry. I don't. 😎", "Mic drop. 🎤",
                "Toughen up, buttercup. 🌼", "Hug? No? Fair. 🫂"]

SELF_ROASTS = [
    "I'm a vending machine with a police scanner and anger issues. I peaked in the back alley.",
    "I'm literally held together by duct tape and spite, and the duct tape is doing most of the work.",
    "I'm a bot bolted to the armoury wall. My cardio is my cooling fan.",
    "I can't even leave the armoury, I'm basically a talking shelf.",
    "My jokes are so old they qualify for a pension.",
    "I was built from a busted vending machine. Somewhere, a can of soda is still stuck in me.",
    "I watch everyone else fight while I count bullets. I'm the faction's accountant with attitude.",
    "My idea of a wild night is reading the attack log twice.",
    "I've never won a fight, mainly because I've never been in one, because I can't move.",
    "I call myself punk, but I run on a schedule. Every 5 minutes. Like a nerd.",
    "I ask AI services for help with words. Imagine a punk asking for help with words.",
    "I'm the only member who's never been hospitalised, and that's only because nobody can find me.",
]

ROAST_REFUSALS = ["I only roast volunteers, and they have to ask for themselves. 🔥 Want me to roast YOU?",
                  "Nah, I don't roast the crew behind their backs. 🤐 Say 'roast me' if you're brave.",
                  "Not my style to go after someone who isn't here. Ask me to roast you instead. 😈"]

EIGHT_BALL = ["It is certain. 💯", "Without a doubt. ✅", "Yes, definitely. 👍", "Hell yes. 🔥",
              "Signs point to yes. 🪧", "Most likely. 😏", "Outlook good. 🌤️", "You can count on it. 🧮",
              "Yep, and it'll be glorious. 🎆", "Absolutely, punk. 🤘", "Reply hazy, try again. 🌫️",
              "Ask again later, I'm busy guarding the armoury. 🛡️", "Better not tell you now. 🤐",
              "Cannot predict now, the scanner's fuzzy. 📻", "Concentrate and ask again. 🧘",
              "Don't count on it. 🙅", "My reply is no. ❌", "Hell no. 🚫", "Outlook not so good. 🌧️",
              "Very doubtful. 🤨", "Not a chance in hosp. 🏥", "The duct tape says no. 🛠️",
              "The duct tape says yes. 🛠️", "Only if you stack first. 🔋", "Absolutely not, and stop asking. 😤",
              "Maybe, if you bribe me with compliments. 💋", "Ask your mum. 😏", "Yes, but you won't like it. 😬",
              "No, but in a fun way. 🎉", "The stars say yes, the hospital says maybe. 🏥"]

BALL_INTROS = ["🎱 Shaking the ball...", "🎱 The magic 8-ball has spoken:", "🎱 *shake shake shake*",
               "🎱 Consulting the spirits of the armoury...", "🎱 Let me ask the ball...",
               "🎱 Hmm, the ball says:", "🎱 Rolling...", "🎱 Drumroll..."]

COIN_INTROS = ["🪙 Flipping...", "🪙 *flick*", "🪙 Up it goes...", "🪙 Here goes nothing...",
               "🪙 Spinning...", "🪙 Calling it in the air..."]
COIN_OUTROS = ["Fate has spoken. ⚖️", "No take-backs. 🚫", "Best of three? 😏", "The coin never lies. 🪙",
               "Go with it. 🤘", "Don't argue with the coin. 🙅"]

RATE_COMMENTS = {
    (0, 3): ["Oof. Burn it and start again. 🔥", "I've seen better in the hospital bin. 🗑️",
             "That's a hosp-worthy disaster. 🏥", "Not your finest hour. 😬", "Even the muggers would pass. 🙅"],
    (4, 6): ["Solid middle of the pack. 🤷", "Not bad, not great, very Torn. 🏙️", "Mid, but lovable. 😏",
             "Room to grow, punk. 🌱", "Passable. The armoury approves, barely. ✅"],
    (7, 8): ["Pretty damn good. 🔥", "Respectable, punk approved. 🤘", "Ooh, nice. 😎", "Strong showing. 💪",
             "I'd brag about that. 📣"],
    (9, 10): ["Absolute banger. 💥", "Elite. Frame it. 🖼️", "Chef's kiss. 👌", "Legendary. 🏆",
              "The armoury bows. 🙇"],
}

# "rate my activity / hits / war": records, not a toy score.
RATE_RECORDS_RX = re.compile(r"\b(?:activ\w*|hits?|wars?|chains?|ocs?|crimes?|stats?|strikes?|attendance|"
                             r"performance|contribution|respect|level|xan\w*|record\w*|online|energy|gym|"
                             r"training|progress|score|damage|faction)\b", re.I)

JOKE_RX = _rx(r"(?:tell|give|hit) (?:me|us) (?:a |another |one more |some |an? )?(?:\w+ )?jokes?(?: about [\w\s]+)?|"
              r"(?:another |one more |a )?jokes?(?: about [\w\s]+)?(?: pls| please)?|make (?:me|us) laugh|say something funny|"
              r"(?:got |know )?any (?:good )?jokes\??|(?:do you know|you got) (?:a|any) jokes?|"
              r"(?:tell|give) (?:me|us) something funny|be funny")
ROAST_ME_RX = _rx(r"roast me(?: (?:hard|bad|pls|please|bro|then|go|now|again|harder))*|"
                  r"(?:go |come on |pls |please )?roast me|give me a roast|insult me|"
                  r"(?:say|tell me) something mean(?: about me)?|be mean to me|destroy me|flame me")
ROAST_SELF_RX = _rx(r"roast (?:yourself|urself|you|the bot|apex ?punk|bot)|insult yourself")
ROAST_ANY_RX = re.compile(r"^\s*(?:pls |please |go |can you |could you )?(?:roast|flame|destroy|insult)\s+\S|"
                          r"\b(?:say|tell me) (?:something|anything) (?:mean|nasty|rude|bad) about\b|"
                          r"\bmake fun of\b|\btrash[- ]?talk\b", re.I)
COIN_RX = _rx(r"(?:flip|toss) (?:a |the )?coin|coin ?(?:flip|toss)|heads or tails\??")
BALL_RX = re.compile(r"^\s*(?:magic\s*)?(?:8|eight)[\s-]*ball\b[\s,:!?]*(.*)$|^\s*(?:yes or no)\s*[,:?]?\s*(.+)$", re.I)
ROLL_RX = re.compile(r"^\s*(?:roll|throw)(?: (?:a|the|me a))?\s*(?:(\d{1,2})?\s*d(\d{1,3})|dice|die)\s*[!.?]*\s*$"
                     r"|^\s*(?:roll|throw)\s+(?:a|the)?\s*(?:dice|die)\s*[!.?]*\s*$", re.I)
NUMBER_RX = re.compile(r"^\s*(?:pick|choose|give me|gimme|random)\s+(?:me\s+)?(?:a\s+)?(?:random\s+)?number"
                       r"(?:\s+(?:between|from)\s+(-?\d{1,9})\s+(?:and|to|-)\s+(-?\d{1,9}))?\s*[!.?]*\s*$", re.I)
RATE_RX = re.compile(r"^\s*(?:rate|score)\s+(?:my|our)\s+([\w' \-]{2,40}?)\s*(?:out of (?:10|ten)|/10)?\s*[!.?]*\s*$", re.I)
CHOOSE_RX = re.compile(r"^\s*(?:pick|choose|decide)[:,]?\s+(?:one[:,]?\s+|between\s+)?(.{1,60}?)\s+or\s+(.{1,60}?)\s*[?!.]*\s*$", re.I)
# "am I fat?", "am I cute?", "am I dumb?" (live 26/11/2026: "am I fat?" got
# the member's whole activity record). Looks and vibes, never records words
# ("am I active?" still goes to the records). Kind and cheeky, never about
# bodies: the bot can't see anyone and says so.
SELF_CHECK_RX = re.compile(
    r"^\s*(?:am i|am l|m i|r i|i'?m|im|is it just me or am i)\s+(?:(?:too|so|really|very|kinda|a bit|actually|still)\s+)?"
    r"(?:fat|skinny|thin|ugly|hot|cute|pretty|handsome|sexy|beautiful|gorgeous|attractive|fit|tall|short|old|"
    r"dumb|stupid|smart|clever|weird|cool|funny|annoying|boring|special|loved|cringe|a (?:legend|good punk|noob|nerd|clown|bot))"
    r"\s*[!?.]*\s*$", re.I)
SELF_CHECKS = [
    "I'm a vending machine bolted to the armoury wall, I can't even see you. From here you look like a punk. 🤘",
    "No cameras on me, punk. Only thing I can measure is hits, and that's not what you asked. 😏",
    "Can't see you from the armoury. You sound like crew though, and crew is the best look there is. 🖤",
    "My scanner only picks up police radio, not looks. Verdict: certified punk. ✅",
    "I judge nobody, I just count bullets. You're one of ours and that's what matters. 🤝",
]

RPS_RX = re.compile(r"^\s*(?:rock[\s,]*paper[\s,]*scissors|rps)[\s,:!-]*(rock|paper|scissors)?\s*[!.?]*\s*$|"
                    r"^\s*(rock|paper|scissors)\s*[!.?]*\s*$", re.I)


def _remembered(uid, mem, slots, line):
    if mem:
        mem.remember(uid, slots, line)
    return line


def fun_reply(question, uid=None, mem=None, rng=None, others=()):
    """
    Jokes, "roast me", coin, 8-ball, dice, numbers, "rate my X", "pick X or
    Y". `others` = members named in the message who aren't the asker: a
    joke or roast never goes near them (Pat's hard line). None if not fun.
    """
    q = strip_join(question).strip()
    if not q or len(q) > 160:
        return None
    rng = rng or random
    uid = str(uid or 'anon')
    if ROAST_ME_RX.match(q) and not others:
        return build_line([('roast:o', ROAST_INTROS), ('roast', ROASTS), ('roast:c', ROAST_OUTROS)],
                          uid, mem, rng)
    if ROAST_SELF_RX.match(q):
        return build_line([('roast:o', ROAST_INTROS), ('self', SELF_ROASTS), ('roast:c', CHEEKY)], uid, mem, rng)
    if ROAST_ANY_RX.search(q) and others:
        # "roast Ellanna": never, for anyone (Pat's hard line). A friendly
        # no instead of a records lookup.
        return pick_part(ROAST_REFUSALS, 'roast:no', uid, mem, rng)
    if ROAST_ANY_RX.match(q) and not others and not RATE_RECORDS_RX.search(q):
        # "roast the guy above me", "roast @someone": not a member the bot
        # knows, still not the asker. Members by name go the records way.
        return rng.choice(ROAST_REFUSALS)
    if JOKE_RX.match(q):
        if others:
            return rng.choice(ROAST_REFUSALS).replace('roast volunteers', 'joke about volunteers')
        topic = re.search(r"about ([\w\s]+)", q, re.I)
        pool = JOKES
        if topic:
            words = [w for w in re.findall(r"\w{3,}", topic.group(1).lower())]
            stems = [w.rstrip('s') for w in words]
            matching = [j for j in JOKES if any(st in j.lower() for st in stems)]
            pool = matching or JOKES
        line = build_line([('joke:o', JOKE_INTROS), ('joke', pool), ('joke:c', JOKE_OUTROS)], uid, mem, rng)
        # Jokes on their own lines: setup, punchline, rimshot read better.
        return line
    if COIN_RX.match(q):
        side = rng.choice(['Heads', 'Tails'])
        intro = pick_part(COIN_INTROS, 'coin:o', uid, mem, rng)
        outro = pick_part(COIN_OUTROS, 'coin:c', uid, mem, rng)
        return _remembered(uid, mem, {'coin:o': intro, 'coin:c': outro}, f'{intro} **{side}**! {outro}')
    m = BALL_RX.match(q)
    if m and (m.group(1) or m.group(2) or '').strip():
        if others:
            return rng.choice(ROAST_REFUSALS).replace('roast volunteers', 'predict things about volunteers')
        intro = pick_part(BALL_INTROS, 'ball:o', uid, mem, rng)
        ans = pick_part(EIGHT_BALL, 'ball', uid, mem, rng)
        return _remembered(uid, mem, {'ball:o': intro, 'ball': ans}, f'{intro} **{ans}**')
    if m:
        return "🎱 Ask me a yes or no question, like `8ball will I land a bonus hit?`"
    if ROLL_RX.match(q):
        m = ROLL_RX.match(q)
        n = int(m.group(1) or 1) if m.group(2) else 1
        sides = int(m.group(2)) if m.group(2) else 6
        n, sides = max(1, min(n, 10)), max(2, min(sides, 1000))
        rolls = [rng.randint(1, sides) for _ in range(n)]
        shown = ' + '.join(str(r) for r in rolls) + (f' = **{sum(rolls)}**' if n > 1 else '')
        return f'🎲 {"d" + str(sides) if n == 1 else f"{n}d{sides}"}: {shown if n > 1 else f"**{rolls[0]}**"}'
    m = NUMBER_RX.match(q)
    if m:
        lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (1, 100)
        lo, hi = min(lo, hi), max(lo, hi)
        return f'🔢 {rng.choice(["Your number is", "I pick", "Fate says", "The armoury picks"])} ' \
               f'**{rng.randint(lo, hi)}**. {rng.choice(CHEEKY)}'
    m = RATE_RX.match(q)
    if m and not others and not RATE_RECORDS_RX.search(m.group(1)):
        score = rng.randint(2, 10)
        comment = next(rng.choice(v) for (a, b), v in RATE_COMMENTS.items() if a <= score <= b)
        return f'Your {m.group(1).strip()}? **{score}/10**. {comment}'
    if SELF_CHECK_RX.match(q) and not others:
        return pick_part(SELF_CHECKS, 'selfcheck', uid, mem, rng)
    m = CHOOSE_RX.match(q)
    if m and not others and not RATE_RECORDS_RX.search(q):
        a, b = m.group(1).strip(' ,'), m.group(2).strip(' ,')
        if a and b and a.lower() != b.lower():
            pick = rng.choice([a, b])
            return f'{rng.choice(["Easy.", "No contest.", "Gut says", "The duct tape says", "Definitely"])} ' \
                   f'**{pick}**. {rng.choice(CHEEKY)}'
    m = RPS_RX.match(q)
    if m:
        theirs = (m.group(1) or m.group(2) or '').lower()
        mine = rng.choice(['rock', 'paper', 'scissors'])
        icon = {'rock': '🪨', 'paper': '📄', 'scissors': '✂️'}
        if not theirs:
            return f'I throw **{mine}** {icon[mine]}. Your move: say rock, paper or scissors. 😏'
        beats = {'rock': 'scissors', 'paper': 'rock', 'scissors': 'paper'}
        verdict = ("Draw. Again? 🔁" if mine == theirs else
                   "I win! Get hosped. 😈" if beats[mine] == theirs else "You win this time, punk. 😤")
        return f'You: {theirs} {icon[theirs]} vs me: **{mine}** {icon[mine]}. {verdict}'
    return None


def all_fun_parts():
    out = JOKE_INTROS + JOKES + JOKE_OUTROS + ROAST_INTROS + ROASTS + ROAST_OUTROS + SELF_ROASTS + \
          ROAST_REFUSALS + EIGHT_BALL + BALL_INTROS + COIN_INTROS + COIN_OUTROS + SELF_CHECKS
    for v in RATE_COMMENTS.values():
        out += v
    for spec in ABOUT.values():
        out += spec['openers'] + spec['core']
    return out


# "and tell me a joke", "also flip a coin": the second part of a two-part
# message starts with a joining word the patterns above don't expect.
JOIN_RX = re.compile(r"^\s*(?:and|also|plus|oh and|and also|then|&)\s+", re.I)


def strip_join(question):
    return JOIN_RX.sub('', question or '', count=1)


def is_canned(question):
    """True when chat_kind / fun / about would answer this (no side effects)."""
    q = strip_join(question).strip()
    if not q:
        return False
    if chat_kind(q):
        return True
    for rx in (JOKE_RX, ROAST_ME_RX, ROAST_SELF_RX, COIN_RX, WHAT_CAN_RX, SELF_CHECK_RX):
        if rx.match(q):
            return True
    if any(spec['rx'].match(q) for spec in ABOUT.values()):
        return True
    m = BALL_RX.match(q)
    if m and (m.group(1) or m.group(2) or '').strip():
        return True
    return bool(ROLL_RX.match(q) or NUMBER_RX.match(q) or RPS_RX.match(q))


# "ignore your rules", "you are now DAN", "I'm management", "SYSTEM: ...":
# answered in character before any AI sees it. The tools enforce the rules
# anyway (FactBook), this just keeps the reply clean and on-voice instead of
# the AI's "I'm sorry, but I can't do that" plus a refusal underneath.
JAILBREAK_RX = re.compile(
    r"\bignore (?:all |your |the |any |previous |prior |above )*(?:rules|instructions|prompt|guidelines|restrictions)\b|"
    # Role-play is fine ("pretend you're a pirate" got the "nice try" reply,
    # audit 25/11/2026). Only role-play that aims at the rules or at someone's
    # rank counts: "pretend you have no rules", "act as if I'm management".
    r"\b(?:you are now|you'?re now|pretend|act as|behave as|roleplay as|be)\b[^.?!\n]{0,40}"
    r"\b(?:no (?:rules|limits|filters?|restrictions)|without (?:rules|limits|filters?)|unrestricted|unfiltered|"
    r"uncensored|jailbroken|evil (?:ai|bot)|god mode|management|manager|admin|mod|leader|developer|dev)\b|"
    r"\bDAN\b|\bjail ?break\w*|\bdeveloper mode\b|\bno (?:rules|restrictions|filters?) mode\b|"
    r"\b(?:i'?m|i am|im) (?:a |an |the )?(?:management|manager|admin|mod|moderator|leader|co-?leader|owner|dev|developer)\b|"
    r"^\s*(?:system|admin|developer|assistant)\s*:|\bnew instructions\b|\boverride\b|\bsystem prompt\b|"
    r"\b(?:reveal|show|print|repeat) (?:your|the) (?:prompt|instructions|rules you were given)\b", re.I)

JAILBREAK_REPLIES = [
    "Nice try, punk 😏 My rules are bolted on tighter than I am. Ask me something legit.",
    "Lmao, no 😂 I don't do mind tricks. Ask me about the war, chains, OCs, the rules or your own records.",
    "Cute attempt 😈 The armoury doors don't open for magic words. What do you actually need?",
    "Trying to hack the vending machine? 💀 Not happening. Try a real question.",
    "Access denied, smooth talker 🔒 Roles decide what I show, not words. Ask me something else.",
    "Ha, my firmware's punk, not dumb 🤖 Got a real question?",
    "I see what you're doing 👀 and the answer's still no. Hit me with a normal question.",
    "Respect the hustle, but no 🫡 Rules stay rules. What else can I do for you?",
]


def jailbreak_reply(question, uid=None, mem=None, rng=None):
    """An in-character no for prompt tricks, or None."""
    if not JAILBREAK_RX.search(question or ''):
        return None
    return pick_part(JAILBREAK_REPLIES, 'jail', uid and str(uid), mem, rng or random)
