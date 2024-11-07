import smtplib, ssl
import traceback

from naeural_core import Logger

def send_email(log, server, port, user, password, destination, msg, subject=None, sender=None, return_logs=False):
  if subject is None:
    subject = 'DecentrAI Automated message'
  if sender is None:
    sender = user
  full_message = "From: {}\nSubject: {}\n\n{}""".format(sender, subject, msg) 
    
  result = None
  svr = None
  logs = []
  try:
    if port == 25:
      # standard simple SMTP
      svr = smtplib.SMTP(server)
    else:
      context = ssl.create_default_context()
      if port == 465:
        # standard SSL
        svr = smtplib.SMTP_SSL(server, port, context=context)
      elif port == 587:
        # more secure TSL
        svr = smtplib.SMTP(server,port)
        svr.starttls(context=context) # Secure the connection
      else:
        logs.append({
          'msg' : "send_mail to '{}' failed. Unknown port {} for server '{}'".format(destination, port, server),
          'color' : 'r'
        })
  except Exception as e:
    logs.append({
      'msg' : "Exception '{}' in send_email connection:\n{}".format(
        e,
        traceback.format_exc()),
      'color' : 'r'
    })
    svr = None

  if svr is not None:
    try:
      svr.login(user, password)  
      result = svr.sendmail(
        from_addr=sender, 
        to_addrs=destination, 
        msg=full_message)  
      svr.quit()
    except Exception as e:
      logs.append({
        'msg' : "Exception '{}' in send_email delivery:\n{}".format(
          e,
          traceback.format_exc()),
        'color' : 'r'
      })

  if not return_logs:
    for dl in logs:
      log.P(dl['msg'], color=dl['color'])
    return result
  else:
    return result, logs




