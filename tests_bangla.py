from src.bangla import check_bangla, try_fix_mojibake, find_numbers, to_ascii_digits

# 1) healthy Bangla
good = "জাতীয় পরিচয়পত্র প্রাপ্তির জন্য আবেদন করুন। ফি ২৩০ টাকা।"
h = check_bangla(good, expect_bangla=True)
print("1 healthy      -> ok =", h.ok, "| bengali =", h.bengali_chars)

# 2) mojibake round-trip
moji = "পাসপোর্ট আবেদন".encode("utf-8").decode("latin-1")
fixed = try_fix_mojibake(moji)
hm = check_bangla(moji, expect_bangla=True)
print("2 mojibake     -> flagged =", (not hm.ok), "| repaired =", repr(fixed))

# 3) replacement + dotted circle + orphan matra
bad = "জন্ম\ufffd নিবন্ধন \u25ccি"
hb = check_bangla(bad, expect_bangla=True)
print("3 broken chars -> flagged =", (not hb.ok), "|", hb.issues)

# 4) legacy Bijoy: expected Bangla, all-Latin noise (no backticks!)
legacy = "Rvcb wbe>b mbc c>vwßi Rbt Aveb Kiab " * 5
hl = check_bangla(legacy, expect_bangla=True)
print("4 legacy font  -> flagged =", (not hl.ok), "|", hl.issues)

# 5) multi-digit negative-lookbehind scan
s = "টিআইএন ১২৩৪৫৬ ফি ৩,৫০,০০০ টাকা ১৭ ডিজিট 105"
print("5 numbers      ->", find_numbers(s))
print("5 ascii digits ->", to_ascii_digits(s))
