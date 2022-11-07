import os 
import json
import asyncio
from telethon import TelegramClient, events, sync
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import InputPeerEmpty

from datetime import datetime
from babel.dates import format_date
from configparser import ConfigParser

config = ConfigParser()
config.read("auth.conf")


horo_list = ['ОВЕН',
             'ТЕЛЕЦ',
             'БЛИЗНЕЦЫ',
             'РАК',
             'ЛЕВ',
             'ДЕВА',
             'ВЕСЫ',
             'СКОРПИОН',
             'СТРЕЛЕЦ',
             'КОЗЕРОГ',
             'ВОДОЛЕЙ',
             'РЫБЫ']
            
               
class GodnoscopTracker:
    def __init__(self):
        self.godonscopes_path = config["paths"]["gonoscopes_path"]
        self.godnoscopes = self.load_data()
        self.not_updated_text = "Они еще не проапдейтили гороскопы!"

    @staticmethod
    def create_client():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(str(config["auth"]["phone"]),
                                config["auth"]["api_id"],
                                config["auth"]["api_hash"], loop=loop)
        client.start()
        return client
    
    @staticmethod
    def get_last_date():
        from datetime import timedelta
        return datetime.now().date()  # + timedelta(days=1)

    def parse_post(self, post):
        date = format_date(self.get_last_date(), locale="ru", format="d MMMM")
        sign = post.split(".")[0]
        if sign in horo_list:
            if date in post:
                return sign, post
            else:
                return sign, None
        return None
    
    def update_godnoscopes(self):
        self.godnoscopes["last_date"] = str(self.get_last_date())
        self.godnoscopes["data"] = {}
        client = self.create_client()
        for message in client.iter_messages("godnoscopp",
                                            reverse=True,
                                            limit=20,
                                            offset_date=self.get_last_date()):
            parsed = self.parse_post(message.text)
            if parsed is not None:
                sign, post = parsed
                if post is not None:
                    self.godnoscopes["data"][sign] = post 
        self.dump_data()
        client.disconnect()
        return self.godnoscopes

    def load_data(self):
        try:
            with open(self.godonscopes_path) as f:
                data = json.load(f)
        except:
            data = {}
        return data

    def dump_data(self):
        with open(self.godonscopes_path, "w") as f:
            json.dump(self.godnoscopes, f)
        
    def get_horoscope(self, sign):
        if (not self.godnoscopes
                or self.godnoscopes["last_date"] != str(self.get_last_date())
                or not self.godnoscopes["data"]
                or sign not in self.godnoscopes["data"]):

            self.update_godnoscopes()

        return self.godnoscopes["data"].get(sign, self.not_updated_text)
        

if __name__ == "__main__":
    tracker = GodnoscopTracker()

    for sign in horo_list:
        print(tracker.get_horoscope(sign))
        
    tracker.dump_data()
