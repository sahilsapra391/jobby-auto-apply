import json, subprocess

STORE = "/Users/sahilsapra/Documents/Development.nosync/jobby-auto-apply/scripts/store.py"
APPID = "vanguard-india-product-manager-specialist"
TMP = "/Users/sahilsapra/Documents/Development.nosync/jobby-auto-apply/.tmp-vg-session.json"

s = json.loads(subprocess.check_output(["python3", STORE, "session-load", "--id", APPID]).decode())

action = (
    "Press Open & finish. The Vanguard Workday form is open, signed in, and filled through step 3 of 5, "
    "with these three questions highlighted in red. Answer them (each is a Yes/No dropdown), then press "
    "Retry and I carry on through Voluntary Disclosures, Review and submit."
)

s["status"] = "review"
s["phase"] = "Filling the form"
s["phaseDetail"] = "10 of 13 application questions filled; 3 need your answer"
s["pendingFields"] = [
    {
        "field": "Post-employment obligations",
        "question": (
            "Do you have any post-employment obligations to any current or former employer "
            "(i.e., non-compete, non-solicitation, etc.) that may prohibit or restrict your "
            "employment for a position?"
        ),
        "blocker": "answer only you can give",
        "detail": (
            "Required Yes/No dropdown on step 3 of 5 (Application Questions). The answer store has no "
            "entry for non-compete or non-solicitation obligations. The closest stored answer is "
            "'conflicts of interest with any company: No', which is a different disclosure, so it was not used."
        ),
        "action": action,
    },
    {
        "field": "Political contributions in the last 2 years",
        "question": (
            "Securities industry regulations require investment advisory firms to collect data on political "
            "contributions made within the last 2 years. Have you made a political contribution(s) to any "
            "political official, candidate, party, or organization (e.g. Political Action Committee) within "
            "the last 2 years?"
        ),
        "blocker": "answer only you can give",
        "detail": (
            "Required Yes/No dropdown on step 3 of 5 (Application Questions). Nothing in the profile or answer "
            "store covers political contributions, and this is a regulated disclosure, so it was left blank "
            "rather than guessed."
        ),
        "action": action,
    },
    {
        "field": "Relation to a Vanguard client's director or officer",
        "question": (
            "Are you related to, by birth, adoption or marriage, or a close associate of a current or former "
            "director, officer or senior employee of a Vanguard client who has authority to award or materially "
            "influence any decision to award Vanguard business?"
        ),
        "blocker": "answer only you can give",
        "detail": (
            "Required Yes/No dropdown on step 3 of 5 (Application Questions). The store covers relatives who are "
            "government officials and relatives working at Vanguard, but nothing about Vanguard's clients, so it "
            "was left blank."
        ),
        "action": action,
    },
]
s["pendingNote"] = (
    "Workday account gate is cleared: signed in as sahilsapra391@gmail.com with the password Chrome had saved. "
    "My Information and My Experience are saved (India identity block, all six roles, Miami University-Oxford "
    "B.S. Economics, tailored resume uploaded, LinkedIn and personal site). 10 of the 13 application questions "
    "are answered from the store. Three regulated Yes/No disclosures have no source in the profile or answer "
    "store and were left blank rather than invented. The tab is open on Application Questions with all three flagged."
)

with open(TMP, "w") as f:
    json.dump(s, f, indent=2)

out = json.loads(subprocess.check_output(["python3", STORE, "session-save", "--id", APPID, "--input", TMP]).decode())
print(json.dumps({k: out[k] for k in ("applicationId", "status", "phase", "phaseDetail")}, indent=2))
print("pendingFields:", len(out.get("pendingFields", [])))
