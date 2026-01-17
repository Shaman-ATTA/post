import re

with open('bot.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Исправляем индентацию построчно
fixed_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Паттерн: строка начинается с 8 пробелов + if/for/while/etc, но предыдущая строка заканчивается elif
    # и следующая строка имеет 16 пробелов
    
    # Простое исправление - если строка начинается с неправильного числа пробелов
    stripped = line.lstrip()
    leading_spaces = len(line) - len(stripped)
    
    # Пропускаем пустые строки и комментарии
    if stripped.startswith('#') or not stripped.strip():
        fixed_lines.append(line)
        i += 1
        continue
    
    # Если мы внутри process_callback (примерно после строки 600)
    # и строка начинается с неправильной индентацией
    if i > 600 and i < 1900:
        # Если строка elif/if внутри process_callback должна быть с 8 пробелами
        if stripped.startswith('elif data ==') or stripped.startswith('elif data.startswith'):
            if leading_spaces != 8:
                line = '        ' + stripped
        # Внутренности elif должны быть с 12 пробелами
        elif leading_spaces == 8 and not stripped.startswith('elif') and not stripped.startswith('#'):
            # Проверяем предыдущие строки - если выше был elif, то нужно 12 пробелов
            for j in range(i-1, max(i-10, 600), -1):
                prev = fixed_lines[j] if j < len(fixed_lines) else lines[j]
                prev_stripped = prev.lstrip()
                if prev_stripped.startswith('elif data'):
                    line = '            ' + stripped
                    break
                elif prev_stripped.startswith('if data') or prev_stripped.startswith('# '):
                    break
    
    fixed_lines.append(line)
    i += 1

with open('bot.py', 'w', encoding='utf-8') as f:
    f.writelines(fixed_lines)

print(f'Processed {len(fixed_lines)} lines')
