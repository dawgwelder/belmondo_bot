import requests
from bs4 import BeautifulSoup


anecdote_site = "https://www.anekdot.ru/random/anekdot/"


def get_anecdote():
    soup = BeautifulSoup(requests.get(anecdote_site).text, features="lxml")
    text = soup.find("div", {"class": "text"}).text
    return text
