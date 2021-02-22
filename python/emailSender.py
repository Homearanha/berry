import smtplib
from socket import gaierror

sender = 'sender@physics.org'
receiver = 'receiver@physics.org'
smtpserver = 'localhost'
port0 = 1025
login = ''
password = ''

message = """\
From: ricardo.ribeiro@physics.org
To: ricardo.ribeiro@physics.org
Subject: calculation

The calculation is finished.
"""

try:
  with smtplib.SMTP(host=smtpserver,port=port0) as server:
    if login != '':
      server.login(login,password)
    server.sendmail(sender, receiver, message)         
    server.quit()

except (gaierror,ConnectionRefusedError):
  print(' Failed to connect to server.')
except smtplib.SMTPException as e:
  print('SMTP error occurred.')
else:
  print("Successfully sent email")



