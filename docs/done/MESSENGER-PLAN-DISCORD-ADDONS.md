# MESSENGER-PLAN-DISCORD-ADDONS.md
# Дополнение к MESSENGER-PLAN.md — лучшие фичи Discord

> Этот документ — дополнение к основному плану. Все пункты
> пронумерованы как §D1–§D12 и вставляются в соответствующие
> разделы основного документа по ссылкам в каждом пункте.
> Фазы указаны в конце — когда каждую фичу добавлять в §16.

---

## §D1. Форматирование текста (Markdown-lite)

**Вставить в:** §4.3 (модель Message), §8 (POST /messages), §11 (UI)

### Суть
Пользователь пишет `**текст**` — видит **жирный текст**.
Синтаксис набирается в plain-text, рендерится только при отображении.
Сервер хранит сырой Markdown — клиент рендерит.

### Поддерживаемый набор (только это, ничего лишнего)

```
**текст**          → жирный
*текст*            → курсив
__текст__          → подчёркнутый
~~текст~~          → зачёркнутый
`код`              → моноширинный (inline code)
```код```          → блок кода (multiline)
# Текст            → крупный заголовок (только в каналах)
## Текст           → средний заголовок (только в каналах)
> текст            → цитата-врезка (отдельная от reply)
- элемент          → маркированный список
```

**Ограничения по типу чата:**
- `#`, `##` — только в каналах (writer/admin/owner). В личных чатах
  и группах заголовки не нужны, только засоряют.
- Блок кода — везде (студенты IT-специальностей будут рады).
- Всё остальное — везде.

### Реализация — сервер

```python
# models.py — Message
body: str          # сырой Markdown, хранится как есть
body_format: str   # = "markdown" | "plain" (для совместимости со старыми)
```

```python
# routers/messenger.py — POST /chats/{id}/messages
# Валидация перед сохранением:
MAX_BODY_LEN = 4000          # символов
# Запретить опасные конструкции (XSS через Markdown):
# - HTML-теги внутри body — strip полностью
# - ссылки [текст](url) — только whitelist схем: https, http
# Разрешить: все символы Unicode, эмодзи
```

### Реализация — клиент (веб)

```js
// web/src/utils/markdown.js
// Библиотека: marked.js (легковесная) или micromark
// НЕ использовать v-html напрямую — XSS!
// Использовать DOMPurify поверх marked:

import { marked } from 'marked'
import DOMPurify from 'dompurify'

const ALLOWED_TAGS = ['strong','em','u','s','code','pre',
                      'blockquote','ul','li','h1','h2','br','span']
const ALLOWED_ATTR = ['class']

export function renderMarkdown(raw, allowHeadings = false) {
  // Убрать h1/h2 если не канал
  const src = allowHeadings ? raw : raw.replace(/^#{1,2} /gm, '')
  const html = marked.parse(src, { breaks: true, gfm: true })
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    FORBID_TAGS: ['script','style','img','iframe'],
  })
}
```

```vue
<!-- MessageBubble.vue -->
<!-- БЫЛО: {{ message.body }} -->
<!-- СТАЛО: -->
<div class="msg-body"
     v-html="renderMarkdown(message.body, isChannel)"
     @click.stop />
<!-- isChannel = conversation.kind === 'channel' -->
```

```css
/* messenger.css — стили для отрендеренного Markdown */
.msg-body strong { font-weight: 700; }
.msg-body em     { font-style: italic; }
.msg-body u      { text-decoration: underline; }
.msg-body s      { text-decoration: line-through; opacity: 0.7; }
.msg-body code   {
  font-family: 'JetBrains Mono', monospace;
  background: var(--gb-code-bg, rgba(0,0,0,0.12));
  padding: 1px 5px; border-radius: 4px; font-size: 0.88em;
}
.msg-body pre    {
  background: var(--gb-code-bg, rgba(0,0,0,0.12));
  padding: 10px 14px; border-radius: 8px; overflow-x: auto;
  font-family: 'JetBrains Mono', monospace; font-size: 0.85em;
}
.msg-body blockquote {
  border-left: 3px solid var(--gb-accent);
  margin: 4px 0; padding: 2px 10px;
  opacity: 0.8;
}
.msg-body h1 { font-size: 1.4em; font-weight: 800; margin: 8px 0 4px; }
.msg-body h2 { font-size: 1.2em; font-weight: 700; margin: 6px 0 4px; }
```

### Реализация — клиент (десктоп, PySide6)

```python
# ui/messenger/bubble.py
# Qt не поддерживает Markdown нативно.
# Использовать QTextBrowser (рендерит HTML) или
# конвертировать Markdown → HTML на клиенте через markdown2:

import markdown2

def render_body(raw: str, is_channel: bool = False) -> str:
    extras = ['fenced-code-blocks', 'strike', 'underline']
    if is_channel:
        extras.append('header-ids')
    html = markdown2.markdown(raw, extras=extras)
    # Strip заголовки если не канал
    if not is_channel:
        import re
        html = re.sub(r'<h[12][^>]*>.*?</h[12]>', '', html, flags=re.S)
    return html

# QLabel не рендерит всё — используем QTextBrowser в read-only режиме
# или QLabel с subset HTML (только <b><i><u><s><code>)
```

### Тулбар форматирования в композере

```
[ B ] [ I ] [ U ] [ S ] [ <> ] [ """ ] [ > ] | поле ввода | [Отправить]
```

- Кнопки оборачивают выделенный текст нужными символами
- `B` → `**выделение**`, `I` → `*выделение*`, и т.д.
- На мобиле — скрыть тулбар, оставить только через long-press на выделение
- Tooltip на каждой кнопке: «Жирный (Ctrl+B)» и т.п.

### Горячие клавиши (веб)
```
Ctrl+B  → **жирный**
Ctrl+I  → *курсив*
Ctrl+U  → __подчёркнутый__
Ctrl+`  → `код`
```

---

## §D2. Маскот-замедление (Rate Limit с характером)

**Вставить в:** §14 (безопасность/анти-абьюз), §11 (UI), §16 фаза 3

### Суть
Вместо холодного `429 Too Many Requests` — появляется Вектор
с диалоговым облачком. Composer блокируется на `cooldown_seconds`.

### Триггер
```
N = 5 сообщений за M = 8 секунд → rate limit
Первое нарушение: cooldown = 8с
Повторное за 5 мин: cooldown = 20с
Систематическое (3+ раза за 10 мин): cooldown = 60с + тикет модерации
```

### Сервер

```python
# throttle.py — добавить в check_message_rate():
def get_cooldown(user_id: str, db) -> int:
    """Возвращает секунды ожидания. 0 = можно слать."""
    recent = count_messages(user_id, seconds=8)
    if recent < 5:
        return 0
    violations = count_violations(user_id, minutes=10)
    if violations >= 3:
        create_moderation_ticket(user_id, reason='systematic_flood')
        return 60
    if violations >= 1:
        return 20
    record_violation(user_id)
    return 8

# routers/messenger.py — POST /chats/{id}/messages:
cooldown = get_cooldown(current_user.id, db)
if cooldown > 0:
    raise HTTPException(
        status_code=429,
        detail={"cooldown_seconds": cooldown, "mascot": True}
    )
```

### Клиент

```js
// stores/messenger.js
async sendMessage(convId, body) {
  const res = await api.post(`/chats/${convId}/messages`, { body })
  if (res.status === 429 && res.data.mascot) {
    this.cooldown = {
      active: true,
      seconds: res.data.cooldown_seconds,
      remaining: res.data.cooldown_seconds,
    }
    this.startCooldownTimer()
    return
  }
  // обычная обработка
},

startCooldownTimer() {
  const interval = setInterval(() => {
    this.cooldown.remaining--
    if (this.cooldown.remaining <= 0) {
      this.cooldown.active = false
      clearInterval(interval)
    }
  }, 1000)
},
```

```vue
<!-- MascotCooldown.vue — появляется над composer когда cooldown.active -->
<template>
  <Transition name="mascot-slide">
    <div v-if="cooldown.active" class="mascot-cooldown">
      <img :src="mascotFrame" class="mascot-img" />
      <div class="mascot-bubble">
        <span>{{ phrase }}</span>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { getMascotFrame } from '@/utils/mascot'
import { computed, onMounted, ref } from 'vue'

const props = defineProps(['cooldown'])

// Пул фраз по эмоциям
const PHRASES = {
  'предупреж+предупреж': [
    'Эй, полегче! Дай другим слово вставить.',
    'Стоп-стоп-стоп. Подожди немного.',
    'Слишком быстро. Сервер тоже устаёт.',
  ],
  'деф+думает': [
    'Столько мыслей сразу? Дай осмыслить.',
    'Пару секунд — и снова можно.',
    'Я всё записываю, просто чуть медленнее.',
  ],
  'удив+деф': [
    'Ого. Столько за раз?',
    'Рекорд скорости. Но давай сделаем паузу.',
  ],
  'рад+подбадрив': [
    'Вижу энтузиазм! Подожди чуть-чуть — и продолжим.',
  ],
}

// Выбрать случайную комбинацию, не повторять две подряд
const COMBOS = Object.keys(PHRASES)
let lastCombo = null

function pickPhrase() {
  const available = COMBOS.filter(c => c !== lastCombo)
  const combo = available[Math.floor(Math.random() * available.length)]
  lastCombo = combo
  const pool = PHRASES[combo]
  const [emotion, pose] = combo.split('+')
  return {
    text: pool[Math.floor(Math.random() * pool.length)],
    frame: getMascotFrame(emotion, pose),
  }
}

const { text: phrase, frame: mascotFrame } = pickPhrase()
</script>

<style scoped>
.mascot-cooldown {
  display: flex; align-items: flex-end; gap: 10px;
  padding: 8px 16px;
}
.mascot-img { width: 72px; height: 72px; object-fit: contain; }
.mascot-bubble {
  background: var(--gb-surface);
  border: 1.5px solid var(--gb-border);
  border-radius: 16px 16px 16px 4px;
  padding: 10px 14px; font-size: 14px; max-width: 260px;
  position: relative;
}

/* Анимация */
.mascot-slide-enter-active { transition: all .3s ease-out; }
.mascot-slide-leave-active { transition: all .25s ease-in; }
.mascot-slide-enter-from,
.mascot-slide-leave-to   { transform: translateY(20px); opacity: 0; }
</style>
```

---

## §D3. Реакции на сообщения

**Вставить в:** §4 (новая таблица), §6 (действия над сообщением), §8 (API), §11 (UI)

### Модель данных (добавить в §4)

```python
class MessageReaction(Base):
    __tablename__ = 'message_reactions'
    message_id  = Column(String, ForeignKey('messages.id'), primary_key=True)
    user_id     = Column(String, ForeignKey('users.id'),    primary_key=True)
    emoji       = Column(String(8),                         primary_key=True)
    created_at  = Column(DateTime, default=func.now())

    __table_args__ = (
        # emoji — только из whitelist
        CheckConstraint("emoji IN ('👍','✅','❤️','😂','👀','🔥','💯','❓','📌')"),
    )
```

### API (добавить в §8)

```
POST   /messages/{id}/reactions        {emoji}   → добавить реакцию
DELETE /messages/{id}/reactions/{emoji}           → снять свою реакцию
GET    /messages/{id}/reactions                   → список {emoji, count, users[]}
```

### UI

```
Оверлей действий над сообщением (§6) — добавить строку быстрых реакций ПОВЕРХ кнопок:
[ 👍 ][ ✅ ][ ❤️ ][ 😂 ][ 👀 ][ 🔥 ][ 💯 ][ ❓ ][ 📌 ]

Под пузырём сообщения при наличии реакций:
[ 👍 3 ][ ❤️ 1 ]   ← клик по своей = снять, по чужой = добавить ту же
Hover/long-press на счётчик → попап «кто поставил»
```

```vue
<!-- MessageReactions.vue — под пузырём -->
<div class="reactions-bar">
  <button
    v-for="r in groupedReactions"
    :key="r.emoji"
    :class="['reaction-pill', { 'my': r.myReaction }]"
    @click="toggleReaction(r.emoji)"
  >
    {{ r.emoji }} {{ r.count }}
  </button>
</div>
```

---

## §D4. Индикатор набора («печатает…»)

**Вставить в:** §7 (транспорт), §8 (API), §11 (UI)

### Механика на polling (без WS)

```
Клиент: при каждом keystroke в composer → PATCH /chats/{id}/typing
  body: {} (пустой, просто сигнал)
  throttle: не чаще 1 раза в 4 секунды (не на каждую клавишу)
  TTL на сервере: запись живёт 5 секунд, потом автоматически истекает

Сервер: GET /chats/{id}/messages?after=X возвращает дополнительно:
  { ..., "typing_users": ["Иванов И.", "Петрова М."] }

Клиент: показывает под лентой:
  «Иванов И. печатает…»       — один человек
  «Иванов И. и ещё 1 печатают» — двое
  «Несколько человек печатают» — трое и больше
```

### Сервер

```python
# Хранить в Redis (быстро, с TTL) или в таблице TypingIndicator:
class TypingIndicator(Base):
    __tablename__ = 'typing_indicators'
    conversation_id = Column(String, primary_key=True)
    user_id         = Column(String, primary_key=True)
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now())
    # Фоновая задача чистит записи старше 5 секунд

# PATCH /chats/{id}/typing
@router.patch('/chats/{conv_id}/typing')
async def set_typing(conv_id: str, user=Depends(current_user), db=Depends(get_db)):
    upsert_typing(db, conv_id, user.id)   # INSERT OR REPLACE + now()
    return {}

# В GET /chats/{id}/messages — добавить в ответ:
typing = get_active_typers(db, conv_id, exclude_user_id=user.id)
return { 'messages': [...], 'typing_users': [u.display_name for u in typing] }
```

### UI

```vue
<!-- TypingIndicator.vue — добавить в ChatThread.vue под лентой -->
<div v-if="typingUsers.length" class="typing-indicator">
  <span class="typing-dots">
    <span/><span/><span/>    <!-- CSS анимация точек -->
  </span>
  <span class="typing-text">{{ typingText }}</span>
</div>

<script setup>
const typingText = computed(() => {
  const n = props.typingUsers.length
  if (n === 0) return ''
  if (n === 1) return `${props.typingUsers[0]} печатает…`
  if (n === 2) return `${props.typingUsers[0]} и ещё 1 печатают…`
  return 'Несколько человек печатают…'
})
</script>

<style scoped>
.typing-dots span {
  display: inline-block; width: 4px; height: 4px;
  border-radius: 50%; background: var(--gb-muted);
  margin: 0 2px; animation: blink 1.2s infinite;
}
.typing-dots span:nth-child(2) { animation-delay: .2s }
.typing-dots span:nth-child(3) { animation-delay: .4s }
@keyframes blink {
  0%,80%,100% { opacity: .3; transform: scale(.8) }
  40%         { opacity: 1;  transform: scale(1) }
}
</style>
```

---

## §D5. Разделитель «Новые сообщения» + Scroll-to-bottom

**Вставить в:** §11 (UI)

### Разделитель (как «NEW» в Discord)

```js
// stores/messenger.js — при загрузке истории чата:
async loadMessages(convId) {
  const { messages, participant } = await api.get(...)
  const lastReadAt = participant.last_read_at

  // Найти первое непрочитанное
  const firstUnreadIdx = messages.findIndex(
    m => new Date(m.created_at) > new Date(lastReadAt)
  )

  this.messagesWithDivider = messages.map((m, i) => ({
    ...m,
    showNewDivider: i === firstUnreadIdx,
  }))
},
```

```vue
<!-- ChatThread.vue — в цикле сообщений -->
<template v-for="msg in messages" :key="msg.id">
  <div v-if="msg.showNewDivider" class="new-divider">
    <span>Новые сообщения</span>
  </div>
  <MessageBubble :message="msg" />
</template>

<style scoped>
.new-divider {
  display: flex; align-items: center; gap: 12px;
  margin: 12px 0; color: var(--gb-accent); font-size: 12px; font-weight: 600;
}
.new-divider::before, .new-divider::after {
  content: ''; flex: 1; height: 1px; background: var(--gb-accent); opacity: .4;
}
</style>
```

```js
// При открытии чата — скролл к разделителю, а не в самый низ:
nextTick(() => {
  const divider = document.querySelector('.new-divider')
  if (divider) divider.scrollIntoView({ behavior: 'instant', block: 'center' })
  else scrollToBottom()
})
```

### Кнопка «↓ N новых»

```vue
<!-- ScrollToBottomBtn.vue — плавающая кнопка в углу чата -->
<Transition name="fade">
  <button
    v-if="showScrollBtn"
    class="scroll-to-bottom"
    @click="scrollToBottom"
  >
    ↓ <span v-if="unreadCount > 0">{{ unreadCount }}</span>
  </button>
</Transition>

<style scoped>
.scroll-to-bottom {
  position: absolute; bottom: 80px; right: 20px;
  background: var(--gb-surface); border: 1px solid var(--gb-border);
  border-radius: 50%; width: 40px; height: 40px;
  box-shadow: 0 2px 8px rgba(0,0,0,.2);
  cursor: pointer; font-size: 16px;
  display: flex; align-items: center; justify-content: center; gap: 4px;
}
.scroll-to-bottom span {
  background: var(--gb-accent); color: #fff;
  border-radius: 100px; font-size: 11px; font-weight: 700;
  padding: 1px 5px; min-width: 18px; text-align: center;
}
</style>
```

---

## §D6. Системные сообщения в ленте

**Вставить в:** §4.3 (Message.kind), §5 (события групп/каналов), §11 (UI)

### Модель (добавить в Message)

```python
# models.py — Message
kind = Column(String, default='text')
# kind IN ('text', 'system')
# Для system: body — шаблон события:
# 'user_joined:{user_id}:{display_name}'
# 'user_left:{user_id}:{display_name}'
# 'title_changed:{new_title}'
# 'pin_added:{message_id}'
# 'pin_removed:{message_id}'
```

### Когда создавать системные сообщения

```python
# Вставлять system-Message автоматически при:
POST /chats/{id}/members          → 'user_joined:{uid}:{name}'
DELETE /chats/{id}/members/{uid}  → 'user_left:{uid}:{name}'
PATCH /chats/{id}  (title)        → 'title_changed:{new_title}'
POST /messages/{id}/pin           → 'pin_added:{message_id}'
DELETE /messages/{id}/pin         → 'pin_removed:{message_id}'
```

### UI

```vue
<!-- MessageBubble.vue — если msg.kind === 'system' -->
<div v-if="msg.kind === 'system'" class="system-msg">
  <span>{{ formatSystemMsg(msg.body) }}</span>
</div>

<script>
function formatSystemMsg(body) {
  const [type, ...args] = body.split(':')
  const map = {
    user_joined:   (uid, name) => `${name} вступил в чат`,
    user_left:     (uid, name) => `${name} покинул чат`,
    title_changed: (title)     => `Название изменено на «${title}»`,
    pin_added:     ()          => '📌 Сообщение закреплено',
    pin_removed:   ()          => 'Сообщение откреплено',
  }
  return map[type]?.(...args) ?? body
}
</script>

<style scoped>
.system-msg {
  text-align: center; color: var(--gb-muted); font-size: 12px;
  margin: 6px 0; padding: 2px 12px;
}
</style>
```

---

## §D7. Статус пользователя + «На занятии» автоматически

**Вставить в:** §4 (новая таблица UserStatus), §9 (каталог/портфолио), §11 (UI)

### Модель

```python
class UserStatus(Base):
    __tablename__ = 'user_statuses'
    user_id     = Column(String, ForeignKey('users.id'), primary_key=True)
    kind        = Column(String)
    # kind IN ('online','dnd','studying','in_class','away','offline')
    # 'in_class' ставится автоматически планировщиком
    custom_text = Column(String(80), nullable=True)
    # custom_text — только для преподавателей
    until       = Column(DateTime, nullable=True)
    # для 'in_class' — время конца пары
    updated_at  = Column(DateTime, default=func.now())
```

### Автоматический статус «На занятии»

```python
# tasks/status_updater.py — фоновая задача, раз в 5 минут:
def update_class_statuses(db):
    now = datetime.utcnow()
    # Найти все занятия которые идут прямо сейчас (±5 мин)
    active_lessons = db.query(Lesson).filter(
        Lesson.date == now.date(),
        Lesson.start_time <= now.time(),
        Lesson.end_time   >= now.time(),
    ).all()

    for lesson in active_lessons:
        # Преподаватель этого занятия
        set_status(db, lesson.teacher_id,
                   kind='in_class',
                   custom_text=f'На занятии до {lesson.end_time:%H:%M}',
                   until=lesson.end_time_dt)

    # Снять статус 'in_class' у тех, у кого занятие закончилось
    expired = db.query(UserStatus).filter(
        UserStatus.kind == 'in_class',
        UserStatus.until < now,
    ).all()
    for s in expired:
        set_status(db, s.user_id, kind='online')
```

### UI

```
В колонке B (портфолио):
  Иван Иванов
  🟢 Онлайн                 ← обычно
  🎓 На занятии до 13:30    ← автоматически
  🔴 Не беспокоить
  📚 Готовлюсь к экзамену   ← выбирает сам

В списке чатов (колонка A):
  Маленькая иконка статуса на аватаре (4px круг в правом нижнем углу)
  🟢 зелёный / 🔴 красный / 🟡 жёлтый (away/studying)
```

```vue
<!-- StatusBadge.vue — переиспользуется везде -->
<span :class="['status-dot', `status-${status.kind}`]"
      :title="status.custom_text || STATUS_LABELS[status.kind]" />

<style>
.status-dot { display:inline-block; width:10px; height:10px; border-radius:50%; }
.status-online   { background: #22c55e }
.status-dnd      { background: #ef4444 }
.status-studying { background: #f59e0b }
.status-in_class { background: #3b82f6 }
.status-away     { background: #94a3b8 }
.status-offline  { background: #64748b }
</style>
```

---

## §D8. Тихие упоминания (@тихо)

**Вставить в:** §4.3 (Message), §8 (POST /messages), §12 (пуши)

### Синтаксис

```
@Иванов          → обычное упоминание → пуш + бейдж
@!Иванов         → тихое упоминание   → только бейдж, без пуша
```

Или альтернатива: зажать **Shift** при нажатии «Отправить» → все
упоминания в сообщении становятся тихими.

### Модель (добавить в §4.3)

```python
# Message — заменить поле mentions:
mentions = Column(JSON, default=list)
# Формат: [{"user_id": "u:...", "silent": false}, ...]
```

### Сервер — обработка при отправке

```python
# routers/messenger.py — POST /chats/{id}/messages
import re

def parse_mentions(body: str, silent_all: bool = False) -> list[dict]:
    """Парсит @Имя и @!Имя из тела сообщения."""
    mentions = []
    # @!Имя → тихое, @Имя → обычное
    for m in re.finditer(r'@(!?)(\S+)', body):
        silent = bool(m.group(1)) or silent_all
        name   = m.group(2)
        user   = resolve_user_by_name(name)  # поиск по display_name
        if user:
            mentions.append({'user_id': user.id, 'silent': silent})
    return mentions

# При создании сообщения:
msg.mentions = parse_mentions(body, silent_all=request.headers.get('X-Silent-Send'))

# При отправке пушей:
for mention in msg.mentions:
    if not mention['silent']:
        send_push(mention['user_id'], ...)   # пуш только для не-тихих
    else:
        mark_unread(mention['user_id'], ...)  # только бейдж
```

### UI — автодополнение при вводе @

```vue
<!-- Composer.vue — при вводе @ показывать выпадающий список участников -->
<MentionAutocomplete
  v-if="mentionQuery !== null"
  :query="mentionQuery"
  :participants="conversationParticipants"
  @select="insertMention"
/>

<!-- insertMention вставляет @Фамилия в поле ввода -->
<!-- При выборе с зажатым Alt → @!Фамилия (тихое) -->
```

---

## §D9. Поиск внутри чата

**Вставить в:** §8 (API), §11 (UI — ChatHeader)

### API

```
GET /chats/{id}/messages/search?q=текст&limit=20&before_id=
→ { results: [{ ...message, highlights: ["фрагмент с <mark>термином</mark>"] }] }
```

```python
# Серверный поиск — ILIKE по body (PostgreSQL):
db.query(Message).filter(
    Message.conversation_id == conv_id,
    Message.body.ilike(f'%{q}%'),
    Message.deleted_at.is_(None),
).order_by(Message.created_at.desc()).limit(20)
```

### UI

```
В ChatHeader (верхняя полоска чата) — кнопка 🔍
Клик → появляется строка поиска поверх заголовка
Результаты — отдельная панель справа (или заменяет ленту временно)
Клик на результат → прокрутка к сообщению + подсветка
```

---

## §D10. Idempotency Key (client_nonce)

**Вставить в:** §2 (архитектурные решения), §4.3 (Message), §8 (POST /messages)

### Проблема
Пользователь нажал «Отправить», сеть упала на полсекунды, клиент
сделал retry → на сервере два одинаковых сообщения.

### Решение

```python
# models.py — Message:
client_nonce = Column(String(64), nullable=True, unique=True)
# UUID4, генерит клиент при каждой новой отправке
# unique=True → БД отклонит дубликат

# routers/messenger.py — POST /chats/{id}/messages:
existing = db.query(Message).filter_by(client_nonce=body.client_nonce).first()
if existing:
    return existing   # идемпотентный ответ — вернуть уже созданное
# иначе создать новое
```

```js
// stores/messenger.js — sendMessage():
import { v4 as uuidv4 } from 'uuid'

async sendMessage(convId, body) {
  const nonce = uuidv4()
  // Оптимистично добавить в UI сразу
  this.addOptimisticMessage({ id: `pending:${nonce}`, body, pending: true })
  try {
    const msg = await api.post(`/chats/${convId}/messages`,
      { body, client_nonce: nonce })
    // Заменить pending на реальное сообщение
    this.replacePendingMessage(nonce, msg)
  } catch (e) {
    if (e.status !== 429) this.markMessageFailed(nonce)
  }
}
```

---

## §D11. История редактирования (MessageEdit)

**Вставить в:** §2 (архитектурные решения п.16), §4 (модель), §10 (модерация)

### Проблема
Сейчас `Message.edited_at` есть, но модерация не видит что было
до редактирования. Студент пожаловался → автор исправил → тикет
указывает на изменённый текст, а не на оригинал.

### Модель

```python
class MessageEdit(Base):
    __tablename__ = 'message_edits'
    id          = Column(String, primary_key=True, default=lambda: f'edit:{uuid4()}')
    message_id  = Column(String, ForeignKey('messages.id'))
    body_before = Column(Text)   # текст ДО редактирования
    edited_at   = Column(DateTime, default=func.now())
    # body_after = текущий Message.body
```

```python
# При PATCH /messages/{id}:
# 1. Сохранить MessageEdit(message_id, body_before=current_body)
# 2. Обновить Message.body + edited_at
```

### UI

```
На пузыре отредактированного сообщения — тег «(ред.)»
Клик на «(ред.)» → попап «История изменений»:
  14:32 — оригинал: «первый текст»
  14:35 — изменено: «исправленный текст»

Для модерации в тикете:
  Отображать все версии, не только текущую
```

---

## §D12. Автоматические системные каналы

**Вставить в:** §5.3 (каналы), §16 (фазы)

### Суть
При первом входе каждого пользователя система автоматически создаёт
и подписывает его на нужные каналы. Это превращает мессенджер из
«просто чата» в центральный хаб колледжа.

### Три типа автоканалов

**1. «Мои оценки» — личный канал студента**
```python
# При создании студента или первом входе:
channel = create_system_channel(
    title='Мои оценки',
    kind='channel',
    owner='system',
    readers=[student.id],
    writers=['system'],
)

# При выставлении оценки (в существующем коде post_grade()):
post_system_message(
    channel_id=student.grade_channel_id,
    body=f'**{teacher.name}** поставил вам **{grade}** по {subject}'
)
# Студент получает пуш + видит в мессенджере историю всех оценок
```

**2. «Объявления группы» — канал учебной группы**
```python
# При создании группы (автоматически):
channel = create_system_channel(
    title=f'Объявления · {group.name}',
    readers=group.student_ids,
    writers=group.teacher_ids + [admin.id],
)

# Преподаватель в журнале нажимает «Отправить объявление»:
post_to_group_channel(group_id, text)
# → летит в мессенджер всем студентам группы
```

**3. «Расписание» — канал группы для изменений**
```python
# При изменении занятия (перенос/отмена):
if lesson_changed:
    post_system_message(
        channel_id=group.schedule_channel_id,
        body=f'⚠️ Пара **{lesson.subject}** в пятницу '
             f'перенесена в кабинет {lesson.room}'
    )
```

### Признак системного канала в модели

```python
# Conversation:
is_system   = Column(Boolean, default=False)
system_kind = Column(String, nullable=True)
# system_kind IN ('grades', 'announcements', 'schedule', None)
```

```
UI: системные каналы — в отдельной секции списка чатов «Уведомления»,
    нельзя покинуть, нельзя отключить (можно только mute)
```

---

## Фазы — куда вставить в §16

```
Фаза 2 (веб-UI ядра):
  + §D5  Разделитель «Новые сообщения» + scroll-to-bottom
  + §D6  Системные сообщения в ленте (join/leave/pin)
  + §D10 Idempotency key (client_nonce) — сразу, пока не накопились дубли

Фаза 3 (действия над сообщением):
  + §D1  Форматирование текста (Markdown-lite) — сервер + клиент + тулбар
  + §D2  Маскот-замедление (rate limit с Вектором)
  + §D3  Реакции на сообщения

Фаза 4 (жалобы + модерация):
  + §D11 История редактирования (MessageEdit) — нужна модерации

Фаза 5 (группы):
  + §D12 Автоматические системные каналы (оценки, объявления, расписание)

Фаза 7 (WebSocket + presence):
  + §D4  Индикатор набора «печатает…» (сначала polling-версия в ф.2, WS в ф.7)
  + §D7  Статус пользователя + «На занятии» автоматически
  + §D8  Тихие упоминания (@!)

Фаза 8 (пуши + бейджи):
  + §D8  Логика пушей для тихих упоминаний (дополнение к существующей)

v2 (после рабочей текстовой версии):
  + §D9  Поиск внутри чата (дорого по CPU на больших чатах — сначала оценить объём)
```

---

## Что НЕ берём из Discord (дополнение к §18)

```
Slash-команды (боты) · серверные эмодзи · Nitro/буст ·
голосовые и видео-каналы · экранная трансляция ·
встроенный браузер активностей · интеграции сторонних приложений ·
полноценный Markdown (таблицы, изображения через ![](), HTML)
```
