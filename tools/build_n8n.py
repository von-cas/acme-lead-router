"""Writes the n8n workflow files in ../n8n from one place, so names and connections stay consistent.

Run: python3 tools/build_n8n.py
"""
import json
import pathlib
import uuid

OUT = pathlib.Path(__file__).resolve().parent.parent / "n8n"
GHL = "https://services.leadconnectorhq.com"


def nid():
    return str(uuid.uuid4())


class Flow:
    def __init__(self, name):
        self.name, self.nodes, self.conn = name, [], {}

    def node(self, name, type_, version, x, y, params, **extra):
        self.nodes.append({"parameters": params, "id": nid(), "name": name, "type": type_,
                           "typeVersion": version, "position": [x, y], **extra})
        return name

    def link(self, src, dst, output=0):
        outs = self.conn.setdefault(src, {"main": []})["main"]
        while len(outs) <= output:
            outs.append([])
        outs[output].append({"node": dst, "type": "main", "index": 0})

    def write(self, filename, settings):
        data = {"name": self.name, "nodes": self.nodes, "connections": self.conn,
                "settings": settings, "pinData": {}}
        (OUT / filename).write_text(json.dumps(data, indent=2) + "\n")


def http(method, url, body=None, auth=False, headers=None, timeout=10000):
    p = {"method": method, "url": url, "options": {"timeout": timeout}}
    if auth:
        p.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
    if headers:
        p.update({"sendHeaders": True, "headerParameters": {"parameters": [{"name": k, "value": v} for k, v in headers]}})
    if body is not None:
        p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    return p


def if_true(expr):
    return {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                           "conditions": [{"id": nid(), "leftValue": expr, "rightValue": "",
                                           "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                           "combinator": "and"}, "options": {}}


def code(js, each=False):
    return {"mode": "runOnceForEachItem", "jsCode": js} if each else {"jsCode": js}


def slack(text_expr):
    return http("POST", "={{ $vars.SLACK_ALERT_URL }}", body="={{ JSON.stringify({ text: " + text_expr + " }) }}")


HTTP, IF, CODE, SET = "n8n-nodes-base.httpRequest", "n8n-nodes-base.if", "n8n-nodes-base.code", "n8n-nodes-base.set"
GHL_V = [("Version", "2023-02-21")]
SCORED = "$('Scored').item.json"
TAG_URL = "=" + GHL + "/contacts/{{ " + SCORED + ".lead.contact_id }}/tags"
RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000, "onError": "continueErrorOutput"}
LEDGER_NOTE = ("// The delivery ledger lives in this workflow's static data, so the replay and the morning check\n"
               "// (the other two triggers in this workflow) can read it. n8n saves static data only for\n"
               "// production runs of the active workflow, not for manual test runs.\n")

# ---------------------------------------------------------------- STL-01
f = Flow("STL-01 Qualify and Deliver")
f.node("Webhook", "n8n-nodes-base.webhook", 2, 0, 300,
       {"httpMethod": "POST", "path": "acme-lead", "responseMode": "responseNode", "options": {}}, webhookId=nid())
f.node("Key OK?", IF, 2.2, 220, 300, if_true("={{ $json.query.key === $vars.WEBHOOK_KEY }}"))
f.node("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, 440, 480,
       {"respondWith": "json", "responseBody": "={{ JSON.stringify({ error: 'bad key' }) }}", "options": {"responseCode": 401}})
f.node("Respond 202", "n8n-nodes-base.respondToWebhook", 1.1, 440, 300,
       {"respondWith": "json", "responseBody": "={{ JSON.stringify({ received: true }) }}", "options": {"responseCode": 202}})
fields = [("contact_id", "$json.body.customData?.contact_id || $json.body.contact_id"),
          ("first_name", "$json.body.first_name"), ("last_name", "$json.body.last_name"),
          ("email", "$json.body.email"), ("phone", "$json.body.phone"), ("city", "$json.body.city"),
          ("postal_code", "$json.body.customData?.postal_code || $json.body.postal_code"),
          ("monthly_bill", "$json.body.customData?.monthly_bill"), ("homeowner", "$json.body.customData?.homeowner"),
          ("roof_shade", "$json.body.customData?.roof_shade"), ("timeline", "$json.body.customData?.timeline"),
          ("consent", "$json.body.customData?.consent")]
f.node("Lead in", SET, 3.4, 660, 300, {"assignments": {"assignments": [
    {"id": nid(), "name": n, "value": "={{ " + e + " || '' }}", "type": "string"} for n, e in fields]}, "options": {}})
f.node("Qualify", HTTP, 4.2, 880, 300,
       http("POST", "={{ $vars.ROUTER_URL }}/qualify", body="={{ JSON.stringify($json) }}", auth=True, timeout=20000),
       retryOnFail=True, maxTries=2, waitBetweenTries=2000, onError="continueErrorOutput")
f.node("Alert: scoring down", HTTP, 4.2, 1100, 480,
       slack("'Scoring service failed for ' + $('Lead in').item.json.first_name + ': ' + ($json.error?.message || 'unknown') + '. The lead is tagged ai-review. Execution ' + $execution.id"))
f.node("Scored", CODE, 2, 1320, 300, code(
    "// Pass the service's answer through, or build a review-only result when scoring failed.\n"
    "if ($json.qualification && $json.lead) return { json: $json };\n"
    "const lead = $('Lead in').item.json;\n"
    "return { json: {\n"
    "  lead: { ...lead, lead_key: null },\n"
    "  qualification: { score: 0, tier: 'review', reasons: ['Scoring service unavailable'],\n"
    "    summary: 'Scoring service unavailable; review this lead by hand.', source: 'none' },\n"
    "} };\n", each=True))
f.node("Update contact", HTTP, 4.2, 1540, 300, http(
    "PUT", "=" + GHL + "/contacts/{{ $json.lead.contact_id }}", auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ customFields: [\n"
         "  { id: $vars.GHL_FIELD_AI_SCORE, field_value: $json.qualification.score },\n"
         "  { id: $vars.GHL_FIELD_AI_TIER, field_value: $json.qualification.tier },\n"
         "  { id: $vars.GHL_FIELD_AI_SUMMARY, field_value: $json.qualification.summary }\n] }) }}"))
f.node("Add tier tag", HTTP, 4.2, 1760, 300, http(
    "POST", TAG_URL, auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ tags: ['ai-' + " + SCORED + ".qualification.tier] }) }}"))
f.node("Add note", HTTP, 4.2, 1980, 300, http(
    "POST", "=" + GHL + "/contacts/{{ " + SCORED + ".lead.contact_id }}/notes", auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ body: 'AI score ' + " + SCORED + ".qualification.score + '/100 (' + " + SCORED
         + ".qualification.tier + ', ' + ({ rules: 'rules', none: 'not scored' }[" + SCORED
         + ".qualification.source] || 'AI model') + '): ' + " + SCORED
         + ".qualification.summary + ' Reasons: ' + " + SCORED + ".qualification.reasons.join('; ') }) }}"))
f.node("Sellable?", IF, 2.2, 2200, 300, if_true(
    "={{ " + SCORED + ".lead.consent === true && " + SCORED + ".lead.homeowner === true && ['hot', 'warm'].includes("
    + SCORED + ".qualification.tier) }}"))
f.node("Check ledger", CODE, 2, 2420, 200, code(LEDGER_NOTE +
    "const ledger = $getWorkflowStaticData('global');\n"
    "ledger.deliveries = ledger.deliveries || {};\n"
    "const { lead } = $('Scored').item.json;\n"
    "const buyer = 'installer-a';\n"
    "const prior = ledger.deliveries[`${buyer}:${lead.lead_key}`];\n"
    "const thirtyDays = 30 * 24 * 3600 * 1000;\n"
    "if (prior && prior.status === 'delivered' && Date.now() - prior.at < thirtyDays) {\n"
    "  return { json: { action: 'duplicate' } };\n"
    "}\n"
    "const cap = Number($vars.INSTALLER_A_DAILY_CAP || 25);\n"
    "const deliveredToday = Object.values(ledger.deliveries)\n"
    "  .filter(d => d.buyer === buyer && d.status === 'delivered' && d.day === lead.day).length;\n"
    "if (deliveredToday >= cap) {\n"
    "  ledger.deliveries[`${buyer}:${lead.lead_key}`] = { buyer, status: 'held', day: lead.day, at: Date.now(),\n"
    "    attempts: 0, contact_id: lead.contact_id, name: lead.first_name };\n"
    "  return { json: { action: 'cap', deliveredToday, cap } };\n"
    "}\n"
    "return { json: { action: 'deliver', deliveredToday, cap } };\n", each=True))
f.node("Deliver?", IF, 2.2, 2640, 200, if_true("={{ $json.action === 'deliver' }}"))
f.node("Tag skipped", HTTP, 4.2, 2860, 400, http(
    "POST", TAG_URL, auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ tags: [$json.action === 'duplicate' ? 'duplicate-skipped' : 'cap-reached'] }) }}"))
f.node("Map", HTTP, 4.2, 2860, 100, http(
    "POST", "={{ $vars.ROUTER_URL }}/map/installer-a", auth=True,
    body="={{ JSON.stringify({ lead: " + SCORED + ".lead, qualification: " + SCORED + ".qualification }) }}"))
f.node("Deliver", HTTP, 4.2, 3080, 100, http(
    "POST", "={{ $vars.ROUTER_URL }}/mock-buyer/installer-a/leads", auth=True, body="={{ JSON.stringify($json) }}",
    headers=[("Idempotency-Key", "={{ " + SCORED + ".lead.lead_key }}"), ("X-Demo-Outage", "={{ $vars.BUYER_OUTAGE }}")]),
    **RETRY)
f.node("Record delivered", CODE, 2, 3300, 0, code(
    "const ledger = $getWorkflowStaticData('global');\n"
    "ledger.deliveries = ledger.deliveries || {};\n"
    "const { lead } = $('Scored').item.json;\n"
    "ledger.deliveries[`installer-a:${lead.lead_key}`] = { buyer: 'installer-a', status: 'delivered', day: lead.day,\n"
    "  at: Date.now(), attempts: 1, contact_id: lead.contact_id, name: lead.first_name, buyer_lead_id: $json.buyer_lead_id };\n"
    "return { json: { buyer_lead_id: $json.buyer_lead_id } };\n", each=True))
f.node("Tag delivered", HTTP, 4.2, 3520, 0, http(
    "POST", TAG_URL, auth=True, headers=GHL_V, body="={{ JSON.stringify({ tags: ['delivered-installer-a'] }) }}"))
f.node("Record failed", CODE, 2, 3300, 200, code(
    "const ledger = $getWorkflowStaticData('global');\n"
    "ledger.deliveries = ledger.deliveries || {};\n"
    "const { lead } = $('Scored').item.json;\n"
    "const error = $json.error?.message || 'unknown error';\n"
    "ledger.deliveries[`installer-a:${lead.lead_key}`] = { buyer: 'installer-a', status: 'failed', day: lead.day,\n"
    "  at: Date.now(), attempts: 1, contact_id: lead.contact_id, name: lead.first_name,\n"
    "  payload: $('Map').item.json, last_error: error };\n"
    "return { json: { name: lead.first_name, error } };\n", each=True))
f.node("Tag failed", HTTP, 4.2, 3520, 200, http(
    "POST", TAG_URL, auth=True, headers=GHL_V, body="={{ JSON.stringify({ tags: ['delivery-failed'] }) }}"))
f.node("Alert: delivery failed", HTTP, 4.2, 3740, 200, slack(
    "'Delivery failed: ' + $('Record failed').item.json.name + ' to Installer A after 3 tries (' + $('Record failed').item.json.error"
    " + '). The lead is safe in GoHighLevel with tag delivery-failed. Replay runs every 15 minutes. Execution ' + $execution.id"))

# Replay of failed deliveries, every 15 minutes
f.node("Every 15 minutes", "n8n-nodes-base.scheduleTrigger", 1.2, 0, 800,
       {"rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}})
f.node("Pick failed deliveries", CODE, 2, 220, 800, code(
    "const ledger = $getWorkflowStaticData('global');\n"
    "return Object.entries(ledger.deliveries || {})\n"
    "  .filter(([, d]) => d.status === 'failed' && d.attempts < 5)\n"
    "  .map(([key, d]) => ({ json: { key, ...d } }));\n"))
f.node("Deliver again", HTTP, 4.2, 440, 800, http(
    "POST", "={{ $vars.ROUTER_URL }}/mock-buyer/installer-a/leads", auth=True, body="={{ JSON.stringify($json.payload) }}",
    headers=[("Idempotency-Key", "={{ $json.key.split(':')[1] }}"), ("X-Demo-Outage", "={{ $vars.BUYER_OUTAGE }}")]),
    **RETRY)
f.node("Record recovered", CODE, 2, 660, 700, code(
    "const ledger = $getWorkflowStaticData('global');\n"
    "const d = ledger.deliveries[$('Pick failed deliveries').item.json.key];\n"
    "Object.assign(d, { status: 'delivered', attempts: d.attempts + 1, recovered_at: Date.now(), buyer_lead_id: $json.buyer_lead_id });\n"
    "return { json: { contact_id: d.contact_id, name: d.name, attempts: d.attempts } };\n", each=True))
f.node("Remove failed tag", HTTP, 4.2, 880, 700, http(
    "DELETE", "=" + GHL + "/contacts/{{ $json.contact_id }}/tags", auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ tags: ['delivery-failed'] }) }}"))
f.node("Add delivered tag", HTTP, 4.2, 1100, 700, http(
    "POST", "=" + GHL + "/contacts/{{ $('Record recovered').item.json.contact_id }}/tags", auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ tags: ['delivered-installer-a'] }) }}"))
f.node("Alert: recovered", HTTP, 4.2, 1320, 700, slack(
    "'Recovered: ' + $('Record recovered').item.json.name + ' delivered to Installer A on attempt ' + $('Record recovered').item.json.attempts + '.'"))
f.node("Record retry failure", CODE, 2, 660, 900, code(
    "const ledger = $getWorkflowStaticData('global');\n"
    "const d = ledger.deliveries[$('Pick failed deliveries').item.json.key];\n"
    "d.attempts += 1;\n"
    "d.last_error = $json.error?.message || 'unknown error';\n"
    "if (d.attempts >= 5) d.status = 'dead';\n"
    "return { json: { contact_id: d.contact_id, name: d.name, attempts: d.attempts, dead: d.status === 'dead' } };\n", each=True))
f.node("Gave up?", IF, 2.2, 880, 900, if_true("={{ $json.dead }}"))
f.node("Tag dead", HTTP, 4.2, 1100, 900, http(
    "POST", "=" + GHL + "/contacts/{{ $json.contact_id }}/tags", auth=True, headers=GHL_V,
    body="={{ JSON.stringify({ tags: ['delivery-dead'] }) }}"))
f.node("Alert: gave up", HTTP, 4.2, 1320, 900, slack(
    "'Gave up after 5 attempts: ' + $('Record retry failure').item.json.name + ' to Installer A. Needs a person.'"))

# Morning health check
f.node("Every day 8 AM", "n8n-nodes-base.scheduleTrigger", 1.2, 0, 1150, {"rule": {"interval": [{"triggerAtHour": 8}]}})
f.node("Summarize yesterday", CODE, 2, 220, 1150, code(
    "const ledger = $getWorkflowStaticData('global');\n"
    "ledger.deliveries = ledger.deliveries || {};\n"
    "const day = ms => new Date(ms).toLocaleDateString('en-CA', { timeZone: 'America/Chicago' });\n"
    "const yesterday = day(Date.now() - 864e5);\n"
    "// Keep 60 days of history so static data stays small.\n"
    "for (const [key, d] of Object.entries(ledger.deliveries)) {\n"
    "  if (Date.now() - d.at > 60 * 864e5) delete ledger.deliveries[key];\n"
    "}\n"
    "const rows = Object.values(ledger.deliveries).filter(d => d.day === yesterday);\n"
    "const count = s => rows.filter(d => d.status === s).length;\n"
    "const cap = Number($vars.INSTALLER_A_DAILY_CAP || 25);\n"
    "const text = rows.length === 0\n"
    "  ? `No leads were delivered yesterday (${yesterday}). Check the form, workflow 01 and the n8n webhook.`\n"
    "  : `Yesterday (${yesterday}): ${count('delivered')} delivered, ${count('failed')} failed, ${count('dead')} dead, `\n"
    "    + `${count('held')} held at cap. Installer A cap ${count('delivered')}/${cap}.`;\n"
    "return [{ json: { text } }];\n"))
f.node("Post summary", HTTP, 4.2, 440, 1150, slack("$json.text"))

for a, b, o in [("Webhook", "Key OK?", 0), ("Key OK?", "Respond 202", 0), ("Key OK?", "Respond 401", 1),
                ("Respond 202", "Lead in", 0), ("Lead in", "Qualify", 0), ("Qualify", "Scored", 0),
                ("Qualify", "Alert: scoring down", 1), ("Alert: scoring down", "Scored", 0),
                ("Scored", "Update contact", 0), ("Update contact", "Add tier tag", 0), ("Add tier tag", "Add note", 0),
                ("Add note", "Sellable?", 0), ("Sellable?", "Check ledger", 0), ("Check ledger", "Deliver?", 0),
                ("Deliver?", "Map", 0), ("Deliver?", "Tag skipped", 1), ("Map", "Deliver", 0),
                ("Deliver", "Record delivered", 0), ("Deliver", "Record failed", 1),
                ("Record delivered", "Tag delivered", 0), ("Record failed", "Tag failed", 0),
                ("Tag failed", "Alert: delivery failed", 0),
                ("Every 15 minutes", "Pick failed deliveries", 0), ("Pick failed deliveries", "Deliver again", 0),
                ("Deliver again", "Record recovered", 0), ("Deliver again", "Record retry failure", 1),
                ("Record recovered", "Remove failed tag", 0), ("Remove failed tag", "Add delivered tag", 0),
                ("Add delivered tag", "Alert: recovered", 0), ("Record retry failure", "Gave up?", 0),
                ("Gave up?", "Tag dead", 0), ("Tag dead", "Alert: gave up", 0),
                ("Every day 8 AM", "Summarize yesterday", 0), ("Summarize yesterday", "Post summary", 0)]:
    f.link(a, b, o)
f.write("STL-01-qualify-and-deliver.json", {"executionOrder": "v1", "timezone": "America/Chicago",
                                            "saveDataSuccessExecution": "all", "saveDataErrorExecution": "all"})

# ---------------------------------------------------------------- STL-99
e = Flow("STL-99 Error Alert")
e.node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, 0, 0, {})
e.node("Alert: n8n error", HTTP, 4.2, 220, 0, slack(
    "'n8n error in ' + $json.workflow.name + ' at node ' + $json.execution.lastNodeExecuted + ': '"
    " + ($json.execution.error?.message || 'unknown') + '. ' + ($json.execution.url || '')"))
e.link("Error Trigger", "Alert: n8n error")
e.write("STL-99-error-alert.json", {"executionOrder": "v1", "timezone": "America/Chicago"})
print("wrote", sorted(p.name for p in OUT.glob("*.json")))
