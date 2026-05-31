"""
IPO Analyzer - Email Alert System
Sends email when IPO score transitions from NO GO → CONDITIONAL GO or GO
Uses Gmail SMTP with App Password
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
from datetime import datetime

# ─── Configuration ─────────────────────────────────────────────────────────
# To set up Gmail App Password:
# 1. Go to https://myaccount.google.com/apppasswords
# 2. Generate an app password for "Mail"
# 3. Set it as environment variable: set GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
#    Or put it in config/settings.json under "gmail_app_password"

RECIPIENT_EMAIL = "samarth.sharma@outlook.com"
SENDER_EMAIL = os.environ.get("GMAIL_SENDER", "")  # Your Gmail address
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.json')


def _load_config():
    """Load email config from settings.json if env vars not set"""
    global SENDER_EMAIL, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            if not SENDER_EMAIL:
                SENDER_EMAIL = cfg.get('gmail_sender', '')
            if not GMAIL_APP_PASSWORD:
                GMAIL_APP_PASSWORD = cfg.get('gmail_app_password', '')
            if cfg.get('alert_email'):
                RECIPIENT_EMAIL = cfg['alert_email']
    except Exception as e:
        print(f"[Email] Config load error: {e}")


def is_configured():
    """Check if email is properly configured"""
    _load_config()
    return bool(SENDER_EMAIL and GMAIL_APP_PASSWORD and RECIPIENT_EMAIL)


def send_score_change_alert(ipo_name, old_score, new_score, old_decision, new_decision, details=None):
    """
    Send email alert when IPO score changes from NO GO to CONDITIONAL GO or GO.
    
    Args:
        ipo_name: Name of the IPO
        old_score: Previous score (int)
        new_score: New score (int)
        old_decision: Previous decision string ("NO GO", "CONDITIONAL GO", "GO")
        new_decision: New decision string
        details: Optional dict with IPO details for the email body
    """
    _load_config()

    if not is_configured():
        print(f"[Email] Not configured. Set GMAIL_SENDER and GMAIL_APP_PASSWORD env vars or config/settings.json")
        return False

    try:
        # Build email
        msg = MIMEMultipart('alternative')
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = f"IPO Alert: {ipo_name} changed to {new_decision}! (Score: {old_score} -> {new_score})"

        # Plain text version
        text = f"""
IPO Score Change Alert
=====================

IPO: {ipo_name}
Score Change: {old_score}/100 -> {new_score}/100
Decision: {old_decision} -> {new_decision}
Time: {datetime.now().strftime('%d %b %Y, %I:%M %p IST')}
"""

        if details:
            text += f"""
IPO Details:
  Price Band: {details.get('issue_price', 'N/A')}
  Issue Size: {details.get('issue_size_cr', 'N/A')} Cr
  GMP: {details.get('gmp', 'N/A')}
  QIB: {details.get('qib_subscription', 'N/A')}x
  HNI: {details.get('hni_subscription', 'N/A')}x
  Retail: {details.get('retail_subscription', 'N/A')}x
  Open: {details.get('open_date', 'N/A')}
  Close: {details.get('close_date', 'N/A')}
  Status: {details.get('status', 'N/A')}
"""

        # HTML version
        decision_color = '#22c55e' if new_decision == 'GO' else '#eab308'
        html = f"""
<html>
<body style="font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px;">
  <div style="max-width: 500px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 24px; border: 1px solid #334155;">
    <h2 style="color: #60a5fa; margin-top: 0;">IPO Score Change Alert</h2>
    
    <div style="background: #334155; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
      <h3 style="color: white; margin: 0 0 8px 0;">{ipo_name}</h3>
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
          <span style="color: #94a3b8; font-size: 14px;">Score</span><br>
          <span style="font-size: 28px; font-weight: bold; color: white;">{old_score} &rarr; {new_score}</span>
        </div>
        <div style="background: {decision_color}; color: white; padding: 8px 16px; border-radius: 8px; font-weight: bold; font-size: 18px;">
          {new_decision}
        </div>
      </div>
    </div>
"""
        if details:
            html += f"""
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
      <tr><td style="padding: 6px 0; color: #94a3b8;">Price Band</td><td style="text-align: right; color: white; font-weight: 600;">{details.get('issue_price', '—')}</td></tr>
      <tr><td style="padding: 6px 0; color: #94a3b8;">Issue Size</td><td style="text-align: right; color: white; font-weight: 600;">{details.get('issue_size_cr', '—')} Cr</td></tr>
      <tr><td style="padding: 6px 0; color: #94a3b8;">GMP</td><td style="text-align: right; color: #facc15; font-weight: 600;">{details.get('gmp', '—')}</td></tr>
      <tr><td style="padding: 6px 0; color: #94a3b8;">QIB</td><td style="text-align: right; color: #93c5fd; font-weight: 600;">{details.get('qib_subscription', '—')}x</td></tr>
      <tr><td style="padding: 6px 0; color: #94a3b8;">HNI</td><td style="text-align: right; color: #c4b5fd; font-weight: 600;">{details.get('hni_subscription', '—')}x</td></tr>
      <tr><td style="padding: 6px 0; color: #94a3b8;">Retail</td><td style="text-align: right; color: #86efac; font-weight: 600;">{details.get('retail_subscription', '—')}x</td></tr>
      <tr><td style="padding: 6px 0; color: #94a3b8;">Close Date</td><td style="text-align: right; color: white; font-weight: 600;">{details.get('close_date', '—')}</td></tr>
    </table>
"""

        html += f"""
    <p style="color: #64748b; font-size: 12px; margin-top: 16px; border-top: 1px solid #334155; padding-top: 12px;">
      IPO Analyzer Pro &bull; {datetime.now().strftime('%d %b %Y, %I:%M %p')} IST &bull; Auto-alert
    </p>
  </div>
</body>
</html>
"""

        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        print(f"[Email] Alert sent for {ipo_name}: {old_decision} -> {new_decision} (score {old_score}->{new_score})")
        return True

    except Exception as e:
        print(f"[Email] Error sending alert: {e}")
        return False


def get_decision(score):
    """Mirror the frontend scoring thresholds"""
    if score >= 85:
        return 'GO'
    elif score >= 70:
        return 'CONDITIONAL GO'
    return 'NO GO'


def check_and_alert(ipo_name, old_score, new_score, details=None):
    """
    Check if score transition warrants an alert.
    Only alerts on upgrades: NO GO -> CONDITIONAL GO or GO
    """
    old_decision = get_decision(old_score)
    new_decision = get_decision(new_score)

    # Only alert on UPGRADES (not downgrades)
    decision_rank = {'NO GO': 0, 'CONDITIONAL GO': 1, 'GO': 2}
    if decision_rank.get(new_decision, 0) > decision_rank.get(old_decision, 0):
        print(f"[Email] Score upgrade detected for {ipo_name}: {old_decision}({old_score}) -> {new_decision}({new_score})")
        return send_score_change_alert(ipo_name, old_score, new_score, old_decision, new_decision, details)

    return False
