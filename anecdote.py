import requests
from bs4 import BeautifulSoup


anecdote_site = "https://baneks.ru/random"


def get_anecdote():
    soup = BeautifulSoup(requests.get(anecdote_site).text, features="lxml")
    text = soup.find("title").text.split(" |")[0]
    return text
