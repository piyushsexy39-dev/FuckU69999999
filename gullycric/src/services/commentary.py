import random

COMMENTARY_DATABASE = {
    "wicket": [
        "💥 Kya kamaal ki gend, stumps bikhhar gaye—wicket mil gaya!",
        "🔥 Zabardast delivery, batter chakma kha gaya—OUT!",
        "⚡ Seedha nishana stumps par—maidan goonj utha, wicket gir gaya!",
        "WICKET GIRA, KHEL KA RUKH PHIRA 💥",
    ],
    "six": [
        "🚀 CHHAKKAAA! Seedha Sharma ji ki terrace pe! Bada six!",
        "🚁 HELICOPTER SHOT! Wrist ka gajab istemal aur ball building ke paar!",
        "🔥 GOLI KI RAFTAAR! Mohalle ka sabse lamba chhakka!",
        "CHHAKKAAA! Gend ne aasman chhua, maidaan jhoom utha 🚀",
    ],
    "four": [
        "⚡ CHAUKA! Narrow alley se goli ki tarah nikal gayi ball!",
        "🏏 COVER DRIVE! Classic text-book shot deewar ke paar!",
        "💫 EDGED AND FOUR! Slip ke upar se nikal gayi boundary!",
        "CHAUUKA! Ball gayi paar, chaar run shandaar 💥",
    ],
    "dot": [
        "🛡️ DOT BALL! Solid defense! Bowler ne aankh dikhayi.",
        "🐢 BEATEN! Bat ke bahaar se seedha keeper ke haath me!",
        "🎯 GOOD LENGTH! Batsman ne respect di delivery ko.",
        "Ball mein dhaar, scoreboard beqaraar 🎯",
    ],
    "runs": [
        "🏃 Ek run tezi se chura liya!",
        "🏃🏃 Badiya running between the wickets! 2 runs pure kiye!",
        "🏃🏃🏃 Teeno daud lagayi! 3 runs mil gaye!",
    ],
}

COMMENTARY_DATABASE["duck"] = [
    *COMMENTARY_DATABASE["wicket"],
    "Khata na khuli, wicket ki ghanti baji! 🦆",
]

RUN_COMMENTARY = {
    1: "1️⃣ Ek run se shuruaat, jeet ki taraf pehla kadam! 💪",
    2: "2️⃣ Do run ki raftaar, player hai dumdaar! 🔥",
    3: "3️⃣ Teen run ki kamaal chaal, batter ne macha diya dhamaal! 😎",
    4: "4️⃣ CHAUUKAAA! Boundary ke paar, batter ka zabardast vaar! 💥",
    5: "5️⃣ Paanch run ki maar, opposition bhi bole — kya khiladi hai yaar! 😭🔥",
    6: "6️⃣ CHHAKKAAA! Gend gayi aasmaan ke paar, batter hai aaj form mein shandaar! 🚀🏏",
}

RUN_COMMENTARY_ADDITIONS = {
    1: "Ek run aaya, score muskuraya 🏏",
    2: "Do run ki chaal, khel bemisaal ⚡",
    3: "Teen run ki bahaar, badhti score ki raftaar 🏏",
}

def get_run_commentary(runs: int) -> str:
    return RUN_COMMENTARY[runs]


def get_hindi_commentary(event_type: str) -> str:
    pool = COMMENTARY_DATABASE.get(event_type, COMMENTARY_DATABASE["runs"])
    return random.choice(pool)


def get_hindi_delivery_commentary(runs: int) -> str:
    if runs == 0:
        return get_hindi_commentary("dot")
    if runs == 4:
        return get_hindi_commentary("four")
    if runs == 6:
        return get_hindi_commentary("six")
    if runs in RUN_COMMENTARY_ADDITIONS:
        return random.choice((RUN_COMMENTARY[runs], RUN_COMMENTARY_ADDITIONS[runs]))
    return get_run_commentary(runs)
