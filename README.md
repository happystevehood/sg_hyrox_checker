# hyrox_checker
Checks multiple times a day if hyrox tickes are released for sale. 
Only work after the initial sale date, (so can tell what link to check)

config.json configures where to look, also if you want to add new location to monitor need to create a new "*_status.json" file of the form.....

  {
    "SATURDAY I 08.11.2025": {
      "found": true,
      "details": []
    },
    "SUNDAY I 09.11.2025": {
      "found": false,
      "details": []
    }

To run python script only in VS code on my windows machine with miniconda

>C:\Users\Steph\miniconda3\Scripts\activate.bat base
>python check_hyrox_pages.py

To create a summary availability matrix do the following:
>python check_hyrox_pages.py --matrix

but also need to setup secret email/password environment variables etc....
>set  MAIL_USERNAME=
>set  MAIL_PASSWORD=
