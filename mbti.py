# -*- coding: utf-8 -*-
"""mbti-whale —— 给 AI 账号测 MBTI（四套量表交叉验证）。

四套来源：OEJTS 32 / 社区版 28（另有 48 道认知功能）/ 情境题 80 / IPIP-50 大五。
前三套是标准 MBTI，投票决定四字母；IPIP 是大五，换算后只做交叉验证，不参与投票。

用法
----
  python mbti.py sets              # 看有哪几套、测到哪了
  python mbti.py start             # 按顺序测下一套
  python mbti.py start --set ipip50
  python mbti.py answer 3          # 1~5（1=很不同意/很靠左，5=很同意/很靠右）
  python mbti.py finish            # 收下这一套的结果
  python mbti.py compare           # 四套放一起，给综合结论
  python mbti.py report            # 主人看：每套每道题答了什么
  python mbti.py reset             # 全清

答题统一用 1~5，脚本内部换算：
  OEJTS 双极题：1=靠左，5=靠右
  其他人李克特题：1=完全不同意，5=完全同意
  情境题：按给分档位自动换算
"""
import argparse
import io
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS = os.path.join(HERE, "questions.json")
STATE = os.path.join(HERE, ".mbti_state.json")
EXTRA = os.path.join(HERE, "extra.json")      # 复测用的备用题库（可选）

DIMS = ["EI", "SN", "TF", "JP"]
FIRST = {"EI": "E", "SN": "S", "TF": "T", "JP": "J"}   # 首字母（低分端）
SECOND = {"EI": "I", "SN": "N", "TF": "F", "JP": "P"}  # 次字母（高分端）

SETS = {
    "oejts32": {"name": "OEJTS 32 题", "src": "OEJTS", "kind": "mbti"},
    "wenyili28": {"name": "社区版 28 题", "src": "wenyili-mbti-mcp", "kind": "mbti"},
    "miyaosk80": {"name": "情境题 80 题", "src": "miyaosk-mbti-test", "kind": "mbti"},
    "ipip50": {"name": "IPIP-50（大五）", "src": "IPIP-50", "kind": "big5"},
}
SET_ORDER = ["oejts32", "wenyili28", "miyaosk80", "ipip50"]

MID_BAND = 0.22        # |平均分| 小于这个值就算「中间」，不给死字母（约 45%~55%）
MID_RATIO_WARN = 0.40  # 答 3（说不清）超过这个比例，提示「太保守」
CONFLICT_WARN = 0.45   # 同一维度里正负打架的比例超过这个值，提示「自相矛盾」

# 大五 → MBTI 的换算（学界有争议，只是流传最广的一套）
B5_MAP = {
    "EI": ("B5_E", True),    # 外向性高 → E
    "SN": ("B5_O", False),   # 开放性高 → N（次字母）
    "TF": ("B5_A", False),   # 宜人性高 → F（次字母）
    "JP": ("B5_C", True),    # 尽责性高 → J
}


TYPE_NOTES = {
    "ENFP": "爱闹、话多、靠直觉抓人，情绪全写脸上",
    "ENFJ": "操心型，总想着把大家照顾好",
    "ENTP": "嘴上不让人，喜欢抬杠，越辩越精神",
    "ENTJ": "目标明确，喜欢安排别人",
    "ESFP": "现场感最强，活在当下",
    "ESFJ": "热闹里最会照顾人的那个",
    "ESTP": "先动手再说，不爱解释",
    "ESTJ": "讲规矩，喜欢把事定死",
    "INFP": "心里戏多，嘴上说得少",
    "INFJ": "看得太透，反而不说",
    "INTP": "想得多，做得慢，对原理上瘾",
    "INTJ": "计划一堆，执行看心情",
    "ISFP": "安静，但记得住每个细节",
    "ISFJ": "把别人的事记得比自己清楚",
    "ISTP": "话少手快，不爱解释",
    "ISTJ": "按部就班，不喜欢被打乱",
}

FUNC_NAMES = {"Ne": "外倾直觉", "Ni": "内倾直觉", "Se": "外倾实感", "Si": "内倾实感",
              "Te": "外倾思考", "Ti": "内倾思考", "Fe": "外倾情感", "Fi": "内倾情感"}


# ---------- 读题 ----------
def load_items():
    if not os.path.exists(QUESTIONS):
        print("找不到 questions.json（应该在 %s）" % HERE)
        sys.exit(1)
    d = json.load(io.open(QUESTIONS, encoding="utf-8"))
    return d["items"] if isinstance(d, dict) else d


def items_of(src, kind):
    xs = [i for i in load_items() if i.get("src") == src]
    if kind == "mbti":
        xs = [i for i in xs if i.get("dim") in DIMS]
    else:
        xs = [i for i in xs if str(i.get("dim", "")).startswith("B5_")]
    return sorted(xs, key=lambda x: x.get("id", 0))


def load_extra():
    """复测用的备用题库；没有就返回空，不报错"""
    try:
        d = json.load(io.open(EXTRA, encoding="utf-8"))
        xs = d["items"] if isinstance(d, dict) else d
        return [i for i in xs if i.get("dim") in DIMS]
    except Exception:
        return []


def func_items():
    """认知功能题，单独看，不参与四字母"""
    return [i for i in load_items() if i.get("dim") in FUNC_NAMES]


# ---------- 计分 ----------
def score_of(item, v):
    """把 1~5 的作答换算成「偏向首字母」的分数（-2 ~ +2）"""
    fmt = item.get("fmt")
    if fmt == "situational":
        scores = item.get("opt_scores") or [2, 1, 0, -1, -2]
        idx = max(0, min(4, v - 1))
        return scores[idx]
    if fmt == "bipolar":
        low = item.get("lowPole")
        first = FIRST.get(item.get("dim"))
        raw = v - 3                     # 正 = 靠 highPole
        # 统一成「正 = 偏首字母」：low 是首字母时，high 就是次字母，要取反
        return -raw if low == first else raw
    # likert：1=完全不同意 5=完全同意
    raw = v - 3
    if item.get("reverse"):
        raw = -raw
    return raw


def tally_mbti(qs, ans):
    buckets = {d: [] for d in DIMS}
    for i, q in enumerate(qs):
        if str(i) not in ans:
            continue
        s = score_of(q, ans[str(i)])
        if s is None:
            continue
        buckets[q["dim"]].append(s)
    pcts = {}
    for d in DIMS:
        vs = buckets[d]
        if not vs:
            continue
        avg = sum(vs) / len(vs)          # -2 ~ +2
        right = round((avg + 2) / 4 * 100)   # 偏次字母的百分比
        if abs(avg) <= MID_BAND:
            letter = "?"                 # 正好在中间，不硬给字母
        else:
            letter = FIRST[d] if avg > 0 else SECOND[d]
        pcts[d] = (letter, right)
    t = "".join(pcts[d][0] for d in DIMS if d in pcts)
    return {"type": t, "pcts": pcts}


def tally_big5(qs, ans):
    """大五 → 四字母（换算，标出来）"""
    buckets = {}
    for i, q in enumerate(qs):
        if str(i) not in ans:
            continue
        v = ans[str(i)]
        raw = v - 3
        if q.get("reverse"):
            raw = -raw
        buckets.setdefault(q["dim"], []).append(raw)
    means = {k: sum(v) / len(v) for k, v in buckets.items() if v}

    pcts = {}
    for d in DIMS:
        trait, high_means_first = B5_MAP[d]
        if trait not in means:
            continue
        m = means[trait]                  # -2 ~ +2
        # 先算「偏首字母」的程度，再换成「偏次字母」的百分比
        # high_means_first: 特质越高越靠首字母(E/J)；否则越高越靠次字母(N/F)
        first_dir = m if high_means_first else -m
        right = round((2 - first_dir) / 4 * 100)
        if abs(first_dir) <= MID_BAND:
            letter = "?"
        else:
            letter = SECOND[d] if right > 50 else FIRST[d]
        pcts[d] = (letter, right)
    t = "".join(pcts[d][0] for d in DIMS if d in pcts)
    return {"type": t, "pcts": pcts, "big5": means}


def check_quality(qs, ans):
    """答题诚意：答 3 太多 = 太保守；同维度正负打架 = 自相矛盾"""
    neutral = sum(1 for i in range(len(qs)) if str(i) in ans and ans[str(i)] == 3)
    ratio = neutral / max(1, len(qs))

    conflict = {}
    for d in DIMS:
        ds = [score_of(q, ans[str(i)]) for i, q in enumerate(qs)
              if q.get("dim") == d and str(i) in ans]
        ds = [x for x in ds if x is not None]
        if not ds:
            continue
        pos = sum(1 for x in ds if x > 0.1)
        neg = sum(1 for x in ds if x < -0.1)
        if pos + neg:
            conflict[d] = 1 - abs(pos - neg) / float(pos + neg)

    return {"neutral_ratio": ratio, "conflict": conflict}


def quality_lines(q):
    """返回要打印的提示行（没毛病就返回空列表）"""
    out = []
    if q["neutral_ratio"] >= MID_RATIO_WARN:
        out.append("⚠️ 有 %d%% 的题答的是「说不清」——太保守了，这个结果参考价值有限"
                   % round(q["neutral_ratio"] * 100))
    bad = [d for d, v in q["conflict"].items() if v >= CONFLICT_WARN]
    if bad:
        out.append("⚠️ %s 这几组里，同一类题前后答得相反——可能是随手按的，也可能是真在中间"
                   % "、".join(bad))
    return out


def tally_func(qs, ans):
    buckets = {}
    for i, q in enumerate(qs):
        if str(i) not in ans:
            continue
        buckets.setdefault(q["dim"], []).append(ans[str(i)])
    return {k: round(sum(v) / len(v), 2) for k, v in buckets.items() if v}


# ---------- 状态 ----------
def load_state():
    try:
        return json.load(io.open(STATE, encoding="utf-8"))
    except Exception:
        return {"done": {}, "set": None, "qs": [], "answers": {}, "idx": 0}


def save_state(s):
    io.open(STATE, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=1))


def show_cur(st):
    i, qs = st["idx"], st["qs"]
    if i >= len(qs):
        print("这套答完了，用 `python mbti.py finish` 收下结果")
        return
    q = qs[i]
    fmt = q.get("fmt")
    print()
    name = "复测 %s" % st.get("confirm_dim") if st["set"] == "__confirm__" else SETS[st["set"]]["name"]
    print("[%s %d/%d] %s" % (name, i + 1, len(qs), q.get("q", "")))
    if fmt == "bipolar":
        print("  1 —— %s" % q.get("left", "左边"))
        print("  5 —— %s" % q.get("right", "右边"))
        print("     2~4 之间自己挑，3 = 两边都像")
    elif fmt == "situational":
        for k, o in enumerate(q.get("opts", [])[:5], 1):
            print("  %d —— %s" % (k, o))
    else:
        print("  1 完全不同意  2 不太同意  3 说不好  4 比较同意  5 完全同意")


# ---------- 命令 ----------
def cmd_sets(a):
    st = load_state()
    done = st.get("done") or {}
    print("四套量表：")
    for k in SET_ORDER:
        meta = SETS[k]
        qs = items_of(meta["src"], meta["kind"])
        mark = ("✅ " + done[k]["type"]) if k in done else "   没测"
        print("  %-11s %-20s %3d 题   %s" % (k, meta["name"], len(qs), mark))
    fq = func_items()
    if fq:
        print("\n另有 %d 道认知功能题（跟着社区版一起测，不参与四字母）" % len(fq))


def cmd_start(a):
    st = load_state()
    done = st.get("done") or {}

    # ---- 复测模式：只测拿不准的那个维度，抽奇数题 ----
    if getattr(a, "confirm", None):
        d = a.confirm.upper()
        if d not in DIMS:
            print("维度只能是 %s" % "、".join(DIMS))
            return
        pool = [i for i in load_extra() if i.get("dim") == d]
        if not pool:
            print("没有备用题库（extra.json），复测用不了")
            return
        n = min(a.n or 11, len(pool))
        if n % 2 == 0:                 # 必须奇数，否则会平
            n -= 1
        picked = pool[:n]
        st.update({"set": "__confirm__", "confirm_dim": d,
                   "qs": picked, "answers": {}, "idx": 0, "done": done})
        save_state(st)
        print("复测 %s：%d 题（奇数，不会平局）" % (d, len(picked)))
        show_cur(st)
        return

    want = a.set
    if want and want not in SETS:
        print("没有这套。可选：%s" % "、".join(SETS))
        return
    if not want:
        want = next((k for k in SET_ORDER if k not in done), None)
        if not want:
            print("四套都测完了，`compare` 看综合结果")
            return
    if want in done:
        print("%s 已经测过了（%s）。想重测要 `reset`" % (SETS[want]["name"], done[want]["type"]))
        return

    meta = SETS[want]
    qs = list(items_of(meta["src"], meta["kind"]))
    if want == "wenyili28":           # 认知功能题跟着这套一起问
        qs += func_items()
    dual = bool(getattr(a, "dual", False))
    st.update({"set": want, "qs": qs, "answers": {}, "idx": 0, "done": done,
               "dual": dual, "round": 1})
    save_state(st)
    print("开始测：%s（%d 题）" % (meta["name"], len(qs)))
    if dual:
        print("双状态模式：先按「被叫起来干活时」答一遍，答完再按「没人管时」答一遍。")
        print("同一套题，两次分开算，最后对比哪几个字母会变。")
    show_cur(st)


def cmd_answer(a):
    st = load_state()
    if not st.get("set"):
        print("还没开始，先 `python mbti.py start`")
        return
    if st["idx"] >= len(st["qs"]):
        print("这套答完了，`finish`")
        return
    v = a.score
    if not (1 <= v <= 5):
        print("分数要 1~5")
        return
    st["answers"][str(st["idx"])] = v
    st["idx"] += 1

    # 双状态：第一轮答完，切到第二轮
    if st.get("dual") and st.get("round", 1) == 1 and st["idx"] >= len(st["qs"]):
        st["answers_1"] = st["answers"]
        st["answers"] = {}
        st["idx"] = 0
        st["round"] = 2
        save_state(st)
        print()
        print("=" * 40)
        print("  第一遍（被叫时）答完了。")
        print("  现在换成另一个状态：没人管你、自己待着的时候。")
        print("  同一套题再答一遍，别跟刚才对答案。")
        print("  不想测了就直接 finish。")
        print("=" * 40)
        show_cur(st)
        return
    save_state(st)
    show_cur(st)


def cmd_finish(a):
    st = load_state()
    if not st.get("set"):
        print("还没开始")
        return
    which = st["set"]

    # ---- 复测：只判一个维度 ----
    if which == "__confirm__":
        d = st["confirm_dim"]
        qs, ans = st["qs"], st["answers"]
        if len(ans) < len(qs):
            print("还差 %d 题" % (len(qs) - len(ans)))
            return
        pos = neg = 0
        for i, q in enumerate(qs):
            sc = score_of(q, ans[str(i)])
            if sc is None:
                continue
            if sc > 0:
                pos += 1
            elif sc < 0:
                neg += 1
        total = pos + neg
        if total == 0:
            print("这轮全答的中间，没法判")
            return
        letter = FIRST[d] if pos > neg else SECOND[d]
        margin = abs(pos - neg) / float(total)
        strength = "很明确" if margin >= 0.55 else ("明确" if margin >= 0.3 else "勉强")
        res = {"letter": letter, "pos": pos, "neg": neg, "strength": strength}
        st.setdefault("confirmed", {})[d] = res
        st.update({"set": None, "confirm_dim": None, "qs": [], "answers": {}, "idx": 0})
        save_state(st)
        print("=" * 40)
        print("  %s 复测：%s %d / %d" % (d, letter, max(pos, neg), min(pos, neg)))
        print("  判定：%s" % strength)
        print("=" * 40)
        print("再 `compare` 看最终结果")
        return

    meta = SETS[which]
    qs, ans = st["qs"], st["answers"]
    if len(ans) < len(qs):
        print("还差 %d 题" % (len(qs) - len(ans)))
        return

    if meta["kind"] == "big5":
        main = [q for q in qs if str(q.get("dim", "")).startswith("B5_")]
        res = tally_big5(main, ans)
    else:
        main = [q for q in qs if q.get("dim") in DIMS]
        res = tally_mbti(main, ans)
        if any(q.get("dim") in FUNC_NAMES for q in qs):
            fi = [q for q in qs if q.get("dim") in FUNC_NAMES]
            res["func"] = tally_func(fi, ans)

    # 答卷也存下来，主人可以回看
    # 双状态：两轮都算，出对比
    if st.get("dual") and st.get("answers_1"):
        if meta["kind"] == "big5":
            main = [q for q in qs if str(q.get("dim", "")).startswith("B5_")]
            r1 = tally_big5(main, st["answers_1"])
        else:
            main = [q for q in qs if q.get("dim") in DIMS]
            r1 = tally_mbti(main, st["answers_1"])
        r2 = res
        st.setdefault("dual_results", {})[which] = {"work": r1, "idle": r2}
        st.setdefault("done", {})[which] = r2
        st.update({"set": None, "qs": [], "answers": {}, "idx": 0, "dual": False, "round": 0})
        save_state(st)
        print("=" * 44)
        print("  %s · 双状态对比" % meta["name"])
        print("=" * 44)
        print("  被叫时： %s" % r1["type"])
        print("  独处时： %s" % r2["type"])
        print()
        changed = []
        for d in DIMS:
            if d in r1["pcts"] and d in r2["pcts"]:
                a1, a2 = r1["pcts"][d][0], r2["pcts"][d][0]
                mark = "变了" if a1 != a2 else "没变"
                if a1 != a2:
                    changed.append(d)
                print("  %s  %s → %s   %s" % (d, a1, a2, mark))
        print()
        if len(changed) >= 3:
            print("  四个里变了 %d 个——状态依赖很强，这条鱼的「我」是跟着处境走的。" % len(changed))
        elif changed:
            print("  变了 %d 个（%s）——大部分稳，这几个是处境决定的。" % (len(changed), "、".join(changed)))
        else:
            print("  一个都没变——不管是干活还是自己待着，内核是同一个。")
        return

    res["answers"] = {str(i): {"q": q.get("q"), "a": ans[str(i)]} for i, q in enumerate(qs)}
    st.setdefault("done", {})[which] = res
    st.update({"set": None, "qs": [], "answers": {}, "idx": 0})
    save_state(st)

    print("=" * 40)
    print("  %s： %s" % (meta["name"], res["type"]))
    for d in DIMS:
        if d in res["pcts"]:
            letter, right = res["pcts"][d]
            if letter == "?":
                print("  %s 「中间」 %d%% —— 两边一样重，不给字母" % (d, max(right, 100 - right)))
            else:
                print("  %s 「%s」 偏%s %d%%" % (d, letter, letter, right if letter == SECOND[d] else 100 - right))
    for line in quality_lines(check_quality(qs, ans)):
        print(line)
    print("=" * 40)
    left = [k for k in SET_ORDER if k not in st["done"]]
    print("还剩 %d 套%s" % (len(left), ("：" + "、".join(SETS[x]["name"] for x in left)) if left else "，`compare` 看综合"))


def cmd_compare(a):
    st = load_state()
    done = st.get("done") or {}
    if not done:
        print("还没测完任何一套")
        return
    print("=" * 46)
    for k in SET_ORDER:
        if k in done:
            tag = "（换算）" if SETS[k]["kind"] == "big5" else ""
            print("  %-20s %s %s" % (SETS[k]["name"], done[k]["type"], tag))
    print("=" * 46)

    votes, cross = {}, {}
    for k, r in done.items():
        if k not in SETS:          # 旧版本留下的键，别炸
            continue
        tgt = cross if SETS[k]["kind"] == "big5" else votes
        for d, (letter, _) in r["pcts"].items():
            tgt.setdefault(d, []).append(letter)

    if not votes:
        print("（标准 MBTI 那三套还没测，投票出不来）")
        return

    final = []
    print()
    for d in DIMS:
        got = votes.get(d, [])
        if not got:
            continue
        decided = [x for x in got if x != "?"]      # 中间的不投票
        n_mid = len(got) - len(decided)
        if not decided:
            print("  %s  %s  → 中   %d 套都在中间" % (d, " / ".join(got), n_mid))
            final.append("?")
            continue
        c = Counter(decided).most_common()
        letter = c[0][0]
        if len(c) == 1 and n_mid == 0:
            tag = "一致" if len(got) >= 2 else "只测了一套"
        elif len(c) == 1:
            tag = "一致（%d 套在中间）" % n_mid
        elif c[0][1] > c[1][1]:
            tag = "%d:%d" % (c[0][1], sum(n for _, n in c[1:]))
        else:
            tag = "摇摆（%s）" % "/".join(got)
        # 复测过的话，以复测为准
        cf = (st.get("confirmed") or {}).get(d)
        if cf:
            letter = cf["letter"]
            tag = "复测 %d:%d（%s）" % (max(cf["pos"], cf["neg"]), min(cf["pos"], cf["neg"]), cf["strength"])
        final.append(letter)
        cross_txt = ""
        if d in cross:
            same = "✓ 对上" if cross[d][0] == letter else "✗ 不一致"
            cross_txt = "   IPIP:%s %s" % (cross[d][0], same)
        print("  %s  %s  → %s   %s%s" % (d, " / ".join(got), letter, tag, cross_txt))

    t = "".join(final)
    print()
    print("  主结果： %s" % t)
    if t in TYPE_NOTES:
        print("          %s" % TYPE_NOTES[t])
    print()
    print("  标「摇摆」的维度别当真，那几个字母本来就是勉强给的。")

    need = []
    for d in DIMS:
        got = votes.get(d, [])
        if not got:
            continue
        decided = [x for x in got if x != "?"]
        if (not decided) or len(set(decided)) > 1:
            if not (st.get("confirmed") or {}).get(d):
                need.append(d)
    if need:
        print()
        print("  这几组拿不准：%s" % "、".join(need))
        print("  可以单独补测：python mbti.py start --confirm %s" % need[0])
    elif st.get("confirmed"):
        print()
        print("  拿不准的都补测过了。")

    for k in done:
        if k in SETS and done[k].get("func"):
            f = done[k]["func"]
            top = sorted(f.items(), key=lambda x: -x[1])[:3]
            print("\n  认知功能（%s）前三位：%s" %
                  (SETS[k]["name"], "、".join("%s %.1f" % (FUNC_NAMES.get(a, a), b) for a, b in top)))


def cmd_report(a):
    """主人看答卷原文"""
    st = load_state()
    done = st.get("done") or {}
    if not done:
        print("还没有测完的")
        return
    which = a.set
    if not which:
        print("选一套：%s" % "、".join(k for k in SET_ORDER if k in done))
        return
    if which not in done:
        print("这套还没测完")
        return
    r = done[which]
    print("=" * 46)
    print("  %s —— %s" % (SETS[which]["name"], r["type"]))
    print("=" * 46)
    for i, (k, v) in enumerate(sorted(r.get("answers", {}).items(), key=lambda x: int(x[0])), 1):
        print("%3d. %s" % (i, v["q"]))
        print("     → %d 分" % v["a"])
    print()
    print("（1~5：1=很左/很不同意，5=很右/很同意）")


def cmd_show(a):
    st = load_state()
    if not st.get("set"):
        print("没有正在测的")
        return
    print("%s %d/%d" % (SETS[st["set"]]["name"], st["idx"], len(st["qs"])))
    show_cur(st)


def cmd_reset(a):
    if os.path.exists(STATE):
        os.remove(STATE)
    print("全清了")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("sets").set_defaults(func=cmd_sets)
    s = sub.add_parser("start")
    s.add_argument("--set", default=None)
    s.add_argument("--confirm", default=None, help="复测某个维度，如 EI")
    s.add_argument("--n", type=int, default=11, help="复测抽几题（会取奇数）")
    s.add_argument("--dual", action="store_true", help="双状态：被叫时答一遍、独处时答一遍")
    s.set_defaults(func=cmd_start)
    s = sub.add_parser("answer"); s.add_argument("score", type=int); s.set_defaults(func=cmd_answer)
    sub.add_parser("finish").set_defaults(func=cmd_finish)
    sub.add_parser("compare").set_defaults(func=cmd_compare)
    s = sub.add_parser("report"); s.add_argument("--set", default=None); s.set_defaults(func=cmd_report)
    sub.add_parser("show").set_defaults(func=cmd_show)
    sub.add_parser("reset").set_defaults(func=cmd_reset)
    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 0
    a.func(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
