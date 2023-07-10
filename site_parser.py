import requests
from bs4 import BeautifulSoup


anecdote_site = "https://baneks.ru/random"


def get_anecdote():
    soup = BeautifulSoup(requests.get(anecdote_site).text, features="lxml")
    text = soup.find(attrs={"name": "description"})["content"]
    return text


def get_holidays():
    site = "https://kakoysegodnyaprazdnik.ru"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
    }

    response = requests.get(site, headers=headers)

    soup = BeautifulSoup(response.content.decode("utf8"), features="lxml")
    
    print(soup)
    try:
        header = soup.findAll("h2", attrs={"class": "mainpage"})[0].text
        text = header + "\n\n"
    except:
        text = ""

    for value in soup.findAll("span", attrs={"itemprop": "text"}):
        text = f"{text}\n{value.text}"
        
    return text.replace("`", "'")
