name = input("Имя: ").strip()

while True:
    s = input("Желаемая зарплата (₽): ").strip()
    try:
        salary = int(s)
        break
    except ValueError:
        print(f"«{s}» — не число. Введи целое число, например 250000.")

print(f"Привет, {name}! Чтобы получать 400000+ ₽, тебе нужно освоить AI/ML-инженерию.")
