---
Вес: 
Бег: 
ккал: 
---
**Цели**
[[Цели на 2026]]

```dataviewjs
const tasks = dv.current().file.tasks;
const completedTasks = tasks.where(t => t.completed).length;
const totalTasks = tasks.length;
function getColor(percent) {
  if (percent >= 80) return "#9BC53D";
  if (percent >= 60) return "#F9C80E";
  if (percent >= 40) return "#EA3546";
  if (percent >= 20) return "#F86624";
  return "#662E9B";
}
if (totalTasks === 0) {
  dv.paragraph("📝 Задач для отображения прогресса пока нет.");
} else {
  const percent = Math.round((completedTasks / totalTasks) * 100);
  const color = getColor(percent);
  const container = this.container;
  const block = document.createElement("div");
  block.style.marginBottom = "16px";
  const label = document.createElement("div");
  label.textContent = `Прогресс: ${completedTasks} из ${totalTasks} (${percent}%)`;
  label.style.marginBottom = "6px";
  
  const barContainer = document.createElement("div");
  barContainer.style.background = "#eee"; 
  barContainer.style.borderRadius = "8px"; 
  barContainer.style.height = "9px";   
  barContainer.style.width = "100%"; 
  barContainer.style.overflow = "hidden"; 
  const bar = document.createElement("div");
  bar.style.background = color;          
  bar.style.height = "100%";           
  bar.style.width = `${percent}%`;  
  bar.style.transition = "width 0.4s ease-in-out";
  bar.style.borderRadius = percent === 100 ? "8px" : "8px 0 0 8px";
  
  barContainer.appendChild(bar);
  block.appendChild(label);
  block.appendChild(barContainer);
  dv.el("div", block);
}
```
## Задачи на сегодня

- [ ] Подъем
- [ ] Утренняя [тренировка](https://t.me/workoutybot)
- [ ] Душ
- [ ] Стакан теплой воды
- [ ] Массаж глаз
- [ ] Взвеситься
- [ ] Завтрак
- [ ] Полынь
- [ ] Витамины
- [ ] Выложить пост ВКонтакте
- [ ] ПЭК
- [ ] Шахматы
- [ ] Позвонить бабушке и дедушке
- [ ] Обед
- [ ] *Тренировка голоса*
- [ ] *Выложить сторис*
- [ ] Задержка дыхания 5 подходов
- [ ] Вис на турнике 1 минуту
- [ ] Дела
- [ ] Английский - учить слова/видео + приложение
- [ ] [Тренировка](https://t.me/workoutybot)
- [ ] Ужин + овощ
- [ ] Ответить на все комментарии сообщества
- [ ] *Выложить пост ВКонтакте канал*
- [ ] 15 минут жонглирование
- [ ] 10 минут шпагат
- [ ] Душ
- [ ] Ежедневник
- [ ] Дневник самопрограммирования
- [ ] Благодарности
