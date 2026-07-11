#!/usr/bin/env python3
# Telegram inline Approve/Reject gate cho GitHub Actions. KHÔNG cần tài khoản GitHub để duyệt,
# KHÔNG cần gói trả phí. Allowlist theo Telegram user-id (TELEGRAM_APPROVERS_<env>).
# Fail-closed: reject / timeout / lỗi cấu hình => exit 1 (không deploy).
# Bấm bởi user NGOÀI allowlist => báo alert + KHÔNG kết thúc (authorized vẫn bấm duyệt được sau).
import os, sys, time, json, urllib.request, urllib.parse

def env(k, d=""): return (os.environ.get(k) or d).strip()

BOT=env("BOT_TOKEN"); CHAT=env("CHAT_ID")
APPROVAL=env("APPROVAL","true").lower()
if APPROVAL in ("false","0","no","off"):
    print("approval disabled -> auto-pass"); sys.exit(0)
if not BOT or not CHAT:
    print("::error::Thiếu TELEGRAM_BOT_TOKEN/CHAT_ID -> fail-closed. Đặt secret+var để bật gate."); sys.exit(1)

APPROVERS={x.strip() for x in env("APPROVERS").replace(";",",").split(",") if x.strip()}
try: TIMEOUT=max(60,int(float(env("TIMEOUT","1200"))))
except: TIMEOUT=1200
ENVN=env("ENVIRONMENT","?"); IMG=env("IMAGE"); TAG=env("TAG")
RUN_URL=env("RUN_URL"); REVIEW=env("REVIEW_URL"); ACTOR=env("ACTOR")
RUN=env("RUN_ID","0"); ATT=env("RUN_ATTEMPT","1")
API="https://api.telegram.org/bot%s/"%BOT
NONCE="%s.%s"%(RUN,ATT)   # buộc nút chỉ thuộc lần chạy NÀY

def call(method, **params):
    data=urllib.parse.urlencode({k:v for k,v in params.items() if v is not None}).encode()
    last=None
    for _ in range(4):
        try:
            with urllib.request.urlopen(API+method, data=data, timeout=45) as r: return json.load(r)
        except Exception as e: last=e; time.sleep(2)
    print("::warning::telegram %s failed: %s"%(method,last)); return {"ok":False,"description":str(last)}

def callj(method, payload):
    req=urllib.request.Request(API+method, data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json"})
    last=None
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r: return json.load(r)
        except Exception as e: last=e; time.sleep(2)
    print("::warning::telegram %s failed: %s"%(method,last)); return {"ok":False}

# webhook set => getUpdates sẽ 409 => không poll được => fail-closed sớm
wi=call("getWebhookInfo")
if wi.get("ok") and wi.get("result",{}).get("url"):
    print("::error::Bot đang set webhook (%s) -> không long-poll được. Fail-closed."%wi["result"]["url"]); sys.exit(1)

lines=["⏳ *Chờ duyệt deploy* — `%s`"%ENVN, "Image: `%s:%s`"%(IMG,TAG), "Trigger: %s"%(ACTOR or "?")]
if REVIEW: lines.append("Review: %s"%REVIEW)
if RUN_URL: lines.append("Run: %s"%RUN_URL)
lines.append(("✅ Chỉ %d người trong allowlist bấm mới tính — hết hạn %d phút."%(len(APPROVERS),TIMEOUT//60))
             if APPROVERS else "⚠️ Chưa cấu hình approver — bấm để xem Telegram-ID của bạn.")
text="\n".join(lines)
kb={"inline_keyboard":[[{"text":"✅ Approve","callback_data":"apv|"+NONCE},
                       {"text":"❌ Reject","callback_data":"rej|"+NONCE}]]}

# bỏ qua backlog cũ (vẫn bind theo NONCE để an toàn kép)
d=call("getUpdates", timeout=0); offset=None
if d.get("ok") and d.get("result"): offset=d["result"][-1]["update_id"]+1

sent=callj("sendMessage", {"chat_id":CHAT,"text":text,"parse_mode":"Markdown",
                           "reply_markup":kb,"disable_web_page_preview":True})
if not sent.get("ok"):
    print("::error::sendMessage lỗi -> fail-closed:",sent); sys.exit(1)
mid=sent["result"]["message_id"]

def finalize(msg):
    # 1 call: đổi text + gỡ nút. (Tách editMessageReplyMarkup riêng sẽ 400 vì editMessageText đã gỡ nút.)
    callj("editMessageText", {"chat_id":CHAT,"message_id":mid,"text":msg,"parse_mode":"Markdown",
                              "disable_web_page_preview":True,"reply_markup":{"inline_keyboard":[]}})

deadline=time.time()+TIMEOUT; decision=None
while time.time()<deadline:
    poll=min(25, max(1, int(deadline-time.time())))
    resp=call("getUpdates", offset=offset, timeout=poll, allowed_updates=json.dumps(["callback_query"]))
    if not resp.get("ok"): time.sleep(3); continue
    for upd in resp.get("result",[]):
        offset=upd["update_id"]+1
        cq=upd.get("callback_query")
        if not cq: continue
        cbid=cq["id"]; data=cq.get("data","") or ""
        frm=cq.get("from",{}); uid=str(frm.get("id","")); uname=frm.get("username") or frm.get("first_name") or uid
        if "|" not in data or data.split("|",1)[1]!=NONCE:
            call("answerCallbackQuery", callback_query_id=cbid, text="Nút thuộc lần deploy khác/đã cũ."); continue
        action=data.split("|",1)[0]
        if not APPROVERS:
            call("answerCallbackQuery", callback_query_id=cbid, show_alert="true",
                 text="Chưa cấu hình approver. ID của bạn: %s — thêm vào TELEGRAM_APPROVERS_%s rồi re-run."%(uid,ENVN)); continue
        if uid not in APPROVERS:
            call("answerCallbackQuery", callback_query_id=cbid, show_alert="true",
                 text="⛔ Bạn (%s) không có quyền duyệt %s. Bấm không có tác dụng."%(uid,ENVN)); continue  # RETRY-safe
        if action=="apv":
            call("answerCallbackQuery", callback_query_id=cbid, text="✅ Đã duyệt")
            finalize("✅ *APPROVED* `%s` — `%s:%s`\nDuyệt bởi: @%s (%s)\nTrigger: %s\n%s"%(ENVN,IMG,TAG,uname,uid,ACTOR,RUN_URL))
            decision="approved"; break
        if action=="rej":
            call("answerCallbackQuery", callback_query_id=cbid, text="❌ Đã từ chối")
            finalize("❌ *REJECTED* `%s` — `%s:%s`\nBởi: @%s (%s)\n%s"%(ENVN,IMG,TAG,uname,uid,RUN_URL))
            decision="rejected"; break
    if decision: break

if decision=="approved": print("approved"); sys.exit(0)
if decision=="rejected": print("::error::Bị từ chối"); sys.exit(1)
finalize("⏱️ *Hết hạn duyệt* (%dm) — hủy deploy `%s` `%s:%s`\n%s"%(TIMEOUT//60,ENVN,IMG,TAG,RUN_URL))
print("::error::Approval timeout"); sys.exit(1)
