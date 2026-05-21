I want to create a tool to manage my JAV collection. 
I'm using 115 netdisk and saved a lot of JAV videos on it (about 20TB). Now the remained storage is not enough, so that I want you to help me make a tool to manage and review my files on netdisk, including JAV and others. With this tool, I can find the files I don't need more easily. 
The 115 netdisk is mounted to my PC as a local disk, which is E:. The disk is scanned before by "Everything", which is a well-known app on windows to view files. So the tool could use the data from "Everything" without scan the netdisk, which is slow and a little risky because 115 may not want user to scan too frequently. My JAV collection includes censored and uncensored videos. All of them have bango (番号) in the name of video file or contained folder, that you can easily recognize the JAV by it. 

The function I need for the tool:
1. I can view the information (like cover image) of JAV based on the bango. 
    1.1. You can get it from www.javbus.com. For example, https://www.javbus.com/FTHT-332. If the JAV item doesn't exist, which maybe because of the bango is not totally right, give me a button that I can click and search, like this: https://www.javbus.com/search/ssni&type=&parent=ce. For censored and uncensored JAV may be a little different. 
    1.2. There are some method called 刮削, which is to collect and save the information from web. for example: https://github.com/Yuukiy/JavSP. But I hope you'd better choose the method in 1.1. I don't wanna download the information and getting them online is enough. 
2. The files are mainly JAV videos, but some of them maybe some other things like movies, games, or other videos. I want a statics of bangos. Like the rank of same bango series (such as MIDE: 32, SNIS: 20). And list the directory that is not JAV. 
3. The front end should be a web page. How to get the data is up to you. Maybe python script? I know there's Everything SDK for python. 

In my thought, the tool should be like:
1. A pyhthon script using Everything SDK to read files and directories info from Everthing app and then saved into a json file. Because web app could not directly get data from Everything. I don't know if there's some other way to get data from Everything such as command line tools maybe. It's up to you how to get the data.  
2. Open the web page to review the data saved in json generated from previous step. I don't need a backend server. 
3. Every time I want to update the data, just run the python script again. 

p.s. For now, I only want to manage files in E:\115\云下载. It's the folder I want to shrink most. No file is directly saved in 云下载 without being in another folder. Beacause all of them are downloaded from torrent. 
