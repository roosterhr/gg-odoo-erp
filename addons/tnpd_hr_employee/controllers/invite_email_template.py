# Part of TNPD Prison HR Employee Extension.
# License: LGPL-3

"""
TNPD Administrator Invitation Email Template
Placeholders: {{recipient_name}}, {{inviter_name}}, {{role}}, {{login_email}}, {{activation_link}}
"""

INVITE_EMAIL_SUBJECT = "You've been invited to administer the TNPD Prison HRMS"

INVITE_EMAIL_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta http-equiv="X-UA-Compatible" content="IE=edge" />
<title>TNPD Prison HRMS — Administrator Invitation</title>
<!--[if mso]>
<style>table,td,div,h1,h2,h3,p,a{font-family:Arial,Helvetica,sans-serif !important;}</style>
<![endif]-->
<style>
  body { margin:0; padding:0; width:100% !important; -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; background:#EEF1F6; }
  table { border-collapse:collapse; mso-table-lspace:0pt; mso-table-rspace:0pt; }
  img { border:0; line-height:100%; outline:none; text-decoration:none; -ms-interpolation-mode:bicubic; display:block; }
  a { text-decoration:none; }
  .hover-btn:hover { background:#2563EB !important; }
  @media screen and (max-width:620px){
    .container { width:100% !important; }
    .px { padding-left:24px !important; padding-right:24px !important; }
    .stack { display:block !important; width:100% !important; text-align:center !important; }
    .cred-label { width:120px !important; }
    .h1 { font-size:22px !important; }
  }
</style>
</head>
<body>
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#EEF1F6;opacity:0;">
  You have been invited as an Administrator on the TNPD Prison HRMS. Activate your account within 7 days.
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF1F6;">
<tr>
<td align="center" style="padding:32px 16px;">

  <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;background:#FFFFFF;border-radius:16px;overflow:hidden;box-shadow:0 8px 32px rgba(15,23,42,0.10);">

    <!-- Masthead -->
    <tr>
    <td style="background:#0F172A;background:linear-gradient(135deg,#0F172A 0%,#1E3A5F 100%);padding:32px 40px;" class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td valign="middle">
          <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#93B4E6;font-weight:700;">Government of Tamil Nadu</div>
          <div style="font-family:Arial,Helvetica,sans-serif;font-size:19px;color:#FFFFFF;font-weight:700;padding-top:4px;letter-spacing:-0.2px;">Tamil Nadu Prison Department</div>
          <div style="font-family:Arial,Helvetica,sans-serif;font-size:12.5px;color:#B9CBE6;padding-top:3px;">Human Resource Management System</div>
        </td>
      </tr>
      </table>
    </td>
    </tr>

    <!-- Accent rule -->
    <tr><td style="height:4px;background:#1D4ED8;font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- Body -->
    <tr>
    <td style="padding:40px 40px 8px 40px;" class="px">
      <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#1D4ED8;font-weight:700;">Administrator Invitation</div>
      <h1 class="h1" style="font-family:Arial,Helvetica,sans-serif;font-size:26px;line-height:1.25;color:#0F172A;font-weight:700;margin:12px 0 0 0;letter-spacing:-0.4px;">You've been invited to administer the Prison HRMS</h1>
      <p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.65;color:#475569;margin:18px 0 0 0;">
        Dear <strong style="color:#0F172A;">{{recipient_name}}</strong>,
      </p>
      <p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.65;color:#475569;margin:14px 0 0 0;">
        <strong style="color:#0F172A;">{{inviter_name}}</strong> has invited you to join the <strong style="color:#0F172A;">TNPD Prison HRMS</strong> as a <strong style="color:#0F172A;">{{role}}</strong>. This account grants access to personnel records, transfers, grievances and departmental dashboards for your assigned jurisdiction.
      </p>
    </td>
    </tr>

    <!-- Credentials panel -->
    <tr>
    <td style="padding:24px 40px 8px 40px;" class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5FB;border:1px solid #DCE5F2;border-radius:12px;">
      <tr>
        <td style="padding:22px 24px;">
          <div style="font-family:Arial,Helvetica,sans-serif;font-size:10.5px;letter-spacing:1.2px;text-transform:uppercase;color:#64748B;font-weight:700;padding-bottom:14px;">Account Details</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td class="cred-label" width="150" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#64748B;padding:7px 0;">Login Email</td>
              <td style="font-family:'Courier New',monospace;font-size:13.5px;color:#0F172A;font-weight:700;padding:7px 0;">{{login_email}}</td>
            </tr>
            <tr><td colspan="2" style="border-top:1px solid #E2E8F0;font-size:0;line-height:0;">&nbsp;</td></tr>
            <tr>
              <td class="cred-label" width="150" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#64748B;padding:7px 0;">Role</td>
              <td style="font-family:Arial,Helvetica,sans-serif;font-size:13.5px;color:#0F172A;font-weight:700;padding:7px 0;">{{role}}</td>
            </tr>
            <tr><td colspan="2" style="border-top:1px solid #E2E8F0;font-size:0;line-height:0;">&nbsp;</td></tr>
            <tr>
              <td class="cred-label" width="150" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#64748B;padding:7px 0;">Invited By</td>
              <td style="font-family:Arial,Helvetica,sans-serif;font-size:13.5px;color:#0F172A;font-weight:700;padding:7px 0;">{{inviter_name}}</td>
            </tr>
          </table>
        </td>
      </tr>
      </table>
    </td>
    </tr>

    <!-- CTA -->
    <tr>
    <td align="center" style="padding:24px 40px 8px 40px;" class="px">
      <table role="presentation" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center" bgcolor="#1D4ED8" style="border-radius:10px;">
          <a class="hover-btn" href="{{activation_link}}" target="_blank" style="display:inline-block;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;color:#FFFFFF;background:#1D4ED8;border-radius:10px;padding:15px 44px;letter-spacing:0.2px;">Activate Your Account</a>
        </td>
      </tr>
      </table>
      <p style="font-family:Arial,Helvetica,sans-serif;font-size:12.5px;line-height:1.6;color:#94A3B8;margin:16px 0 0 0;">
        This invitation expires in <strong style="color:#475569;">7 days</strong>. If the button does not work, copy and paste this link:
      </p>
      <p style="font-family:'Courier New',monospace;font-size:11.5px;line-height:1.5;color:#1D4ED8;margin:6px 0 0 0;word-break:break-all;">{{activation_link}}</p>
    </td>
    </tr>

    <!-- Security note -->
    <tr>
    <td style="padding:20px 40px 32px 40px;" class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FEF9EC;border:1px solid #F7E2B0;border-radius:10px;">
      <tr>
        <td style="padding:14px 18px;">
          <div style="font-family:Arial,Helvetica,sans-serif;font-size:12.5px;line-height:1.6;color:#8A6D2F;">
            <strong style="color:#7A5C1E;">Security notice.</strong> If you were not expecting this invitation, please ignore this email or report it to your department's IT security cell. Never share your activation link or password with anyone.
          </div>
        </td>
      </tr>
      </table>
    </td>
    </tr>

  </table>

  <!-- Footer -->
  <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;">
  <tr>
  <td style="padding:24px 40px;" class="px" align="center">
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.7;color:#94A3B8;text-align:center;">
      Tamil Nadu Prison Department &mdash; Office of the Director General of Prisons<br/>
      Egmore, Chennai &ndash; 600 008, Tamil Nadu, India
    </div>
    <div style="font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:1.7;color:#B0BAC9;text-align:center;padding-top:10px;">
      This is an automated message from the TNPD Prison HRMS. Please do not reply to this email.<br/>
      &copy; 2026 Government of Tamil Nadu. All rights reserved.
    </div>
  </td>
  </tr>
  </table>

</td>
</tr>
</table>
</body>
</html>
"""
