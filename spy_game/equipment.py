"""Finite-use equipment rules shared by persistence and player-facing views."""
from dataclasses import dataclass


@dataclass(frozen=True)
class EquipmentEffect:
    charges: int
    description: str


EQUIPMENT_EFFECTS = {
    "fake_passport": EquipmentEffect(
        3, "Ваш новый таймер в погоне короче на 20%. Заряд за каждый принятый ход."
    ),
    "radio": EquipmentEffect(
        3,
        "+1 осведомитель лично вам за завершённую совместную операцию. Наденьте до начисления.",
    ),
    "wiretap": EquipmentEffect(
        5,
        "Шанс 20% получить +1 осведомителя при наборе. Заряд только при выпадении бонуса.",
    ),
    "intel_file": EquipmentEffect(
        3, "+2 осведомителя за победу в расследовании «Крот»."
    ),
    "satellite_image": EquipmentEffect(
        3, "+1 к награде тайника; пустой тайник даёт разведданные ×1."
    ),
    "access_code": EquipmentEffect(
        3, "+1 такой же предмет за успешный перехват сигнала."
    ),
}

EQUIPMENT_EXCHANGE_RULE = (
    "Для обмена доступны только новые ненадетые экземпляры. После первого бонуса "
    "предмет нельзя обменять. Снятие сохраняет ресурс; при нуле предмет исчезает."
)
