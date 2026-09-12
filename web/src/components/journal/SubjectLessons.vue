<script setup>
// SubjectLessons — занятия ОДНОГО предмета: тема, тип, дата, оценка.
//
// ━━ ЗАЧЕМ ОТДЕЛЬНЫЙ КОМПОНЕНТ ━━
// Журнал студента и журнал родителя обязаны показывать одно и то же — докстринг
// `/web/parent/journal` прямо обещает «формат один в один» со студенческим. Но вёрстка
// была скопирована в оба файла и уже начала расходиться: у студента оценка рисовалась
// пастельной плашкой (порт из десктопа), у родителя — просто цветным текстом; студент
// писал «Практика №3», родитель — «Практика 3». Разъезд незаметен, пока не открыть два
// кабинета рядом, — а рядом их открывает ровно тот, кому это важнее всех: родитель и
// его ребёнок. Одна вёрстка на оба кабинета закрывает вопрос конструктивно.
//
// ━━ ДВЕ РАСКЛАДКИ, А НЕ ОДНА ПРОКРУЧИВАЕМАЯ ━━
// Таблица из четырёх колонок на 360 px требует примерно вдвое больше ширины, чем есть:
// «Тема» — свободный текст преподавателя. Раньше её оборачивали в `overflow-x-auto`, и
// формально страница вбок не ехала — но за правым краем оказывалась ОЦЕНКА, то есть
// единственное, ради чего журнал и открывают: человек видел «Практика №1 | Введение в…»
// и должен был догадаться прокрутить вбок. Поэтому на телефоне занятие рисуется
// строкой-карточкой, где оценка стоит справа в ПЕРВОЙ строке, а таблица включается с
// `sm`, где ширины на неё хватает.
import { useLocaleStore } from '@/stores/locale'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'

defineProps({
  lessons: { type: Array, default: () => [] },
})

const locale = useLocaleStore()

// `latest` присылает журнал родителя (последняя попытка), `grade` — студенческий.
// Одно правило покрывает оба кабинета, второй ветки заводить не нужно.
const gradeOf = (l) => (l.latest || l.grade || '').trim()
// ⚠️ `data-egg-grade` на клетке оценки ниже — не разметка ради разметки: по ней
// пасхалки журнала (кубик Isaac, внутренний голос Disco Elysium) находят ячейки.
// Снимешь — обе тихо перестанут работать, и ни один тест этого не заметит.

// Плашка оценки — та же, что была в нативном журнале десктопа (ui/dashboards.py удалён
// вместе с Qt; сегодня этот компонент и ЕСТЬ журнал на всех платформах): у числовых —
// пастельный фон поверх тёмного текста; «Н» красным, «✓» акцентом; пусто — прочерк.
const GRADE_BG = { 5: '#DBF0E4', 4: '#DCEFF2', 3: '#FBEFD6', 2: '#FAE0DE' }
const head = (g) => g.split(' ')[0]
const isNumericGrade = (g) => !!GRADE_BG[head(g)]
function gradeChip(g) {
  if (GRADE_BG[head(g)]) return { background: GRADE_BG[head(g)], color: '#0F1B22' }
  if (g === 'Н') return { color: 'var(--gb-red)' }
  if (g === '✓') return { color: 'var(--gb-accent)' }
  return { color: 'var(--gb-text2)' }
}
</script>

<template>
  <p v-if="!lessons.length" class="text-sm text-text3">
    {{ locale.t('journal.noLessonsYet', 'Занятий по этому предмету пока нет.') }}
  </p>

  <template v-else>
    <!-- ТЕЛЕФОН: занятие = строка-карточка, оценка первой по заметности.
         `flex flex-col gap-*`, а не `space-y-*`: в Tailwind 4 у `space-y` нулевая
         специфичность, и такой отступ проигрывает любому конкурирующему margin. -->
    <ul class="flex flex-col gap-2 sm:hidden">
      <li v-for="l in lessons" :key="l.id" class="rounded-md border border-border bg-card2 px-3 py-2">
        <div class="flex items-start justify-between gap-2">
          <!-- min-w-0 обязателен: без него длинная тема не сжимается, а распирает строку
               и уводит оценку за край — ровно та же ловушка flex, что и в шапке карточки. -->
          <span class="min-w-0 break-words text-sm font-medium text-text">{{ l.topic || '—' }}</span>
          <span data-egg-grade class="shrink-0 rounded-md text-center font-title text-base font-bold"
                :class="isNumericGrade(gradeOf(l)) ? 'px-2 py-0.5' : ''"
                :style="gradeChip(gradeOf(l))">{{ gradeOf(l) || '—' }}</span>
        </div>
        <p class="mt-1 text-tiny text-text3">
          {{ l.type }} №{{ l.number }} · {{ l.date || '—' }}
        </p>
      </li>
    </ul>

    <!-- ПЛАНШЕТ И ШИРЕ: привычная таблица. overflow-x оставлен на случай очень длинных
         тем — здесь он уже работает как задумано, а не прячет оценку. -->
    <StickyXScroll class="hidden sm:block">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border2 text-left text-tiny uppercase tracking-wide text-text2">
            <th class="py-2 pr-3 font-semibold">{{ locale.t('journal.colType', 'Тип') }}</th>
            <th class="py-2 pr-3 font-semibold">{{ locale.t('journal.colTopic', 'Тема') }}</th>
            <th class="py-2 pr-3 font-semibold">{{ locale.t('journal.colDate', 'Дата') }}</th>
            <th class="py-2 text-right font-semibold">{{ locale.t('journal.colGrade', 'Оценка') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in lessons" :key="l.id" class="border-b border-border last:border-0">
            <td class="whitespace-nowrap py-2.5 pr-3 text-text3">{{ l.type }} №{{ l.number }}</td>
            <td class="py-2.5 pr-3 text-text">{{ l.topic || '—' }}</td>
            <td class="whitespace-nowrap py-2.5 pr-3 text-text3">{{ l.date || '—' }}</td>
            <td class="py-2.5 text-right">
              <span data-egg-grade class="inline-block min-w-[2.2rem] rounded-md text-center font-title text-base font-bold"
                    :class="isNumericGrade(gradeOf(l)) ? 'px-2 py-0.5' : ''"
                    :style="gradeChip(gradeOf(l))">{{ gradeOf(l) || '—' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </StickyXScroll>
  </template>
</template>
