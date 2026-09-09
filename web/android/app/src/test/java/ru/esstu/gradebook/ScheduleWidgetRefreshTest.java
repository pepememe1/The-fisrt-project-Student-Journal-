package ru.esstu.gradebook;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

/**
 * Самообновление виджета: что можно проверить БЕЗ телефона и без сети.
 *
 * Сетевой поход и рисование проверить в JVM нельзя, а вот три вещи, в которых и живут
 * настоящие ошибки, — можно, и они здесь: заслонка канала, сборка адреса и перевод
 * ответа сервера в формат виджета. Именно на переводе легче всего молча потерять пары
 * (у преподавателя элемент дня — обёртка, а не сама пара) — и заметно это станет уже на
 * рабочем столе у человека.
 */
public class ScheduleWidgetRefreshTest {

    // ───────────────────────── канал ─────────────────────────

    @Test
    public void onlyHttpsIsAccepted() {
        assertTrue(ScheduleWidgetRefresh.transportOk("https://esstu-gradebook.ru"));
        assertTrue("регистр схемы не должен решать", ScheduleWidgetRefresh.transportOk("HTTPS://esstu-gradebook.ru"));
        //По открытому каналу расписание подменяет любой, кто встал в середину, — и человек
        //приходит не на ту пару. В манифесте cleartext разрешён ради локального бандла
        //Capacitor, поэтому без явного отказа такой запрос прошёл бы молча.
        assertFalse(ScheduleWidgetRefresh.transportOk("http://192.168.1.10:8000"));
        assertFalse(ScheduleWidgetRefresh.transportOk(""));
        assertFalse(ScheduleWidgetRefresh.transportOk(null));
    }

    // ───────────────────────── когда ходить ─────────────────────────

    @Test
    public void firstRunGoesImmediately() {
        assertTrue(ScheduleWidgetRefresh.isDue(1_000_000L, 0, 0));
    }

    @Test
    public void freshSuccessBlocksNetworkForSixHours() {
        long t = 1_000_000_000L;
        assertFalse("через минуту после удачи в сеть не идём",
                ScheduleWidgetRefresh.isDue(t + 60_000, t, t));
        assertTrue("через шесть часов — пора",
                ScheduleWidgetRefresh.isDue(t + ScheduleWidgetRefresh.MIN_INTERVAL_MS, t, t));
    }

    @Test
    public void failureIsRetriedSooner() {
        long t = 1_000_000_000L;
        //lastOk старее lastTry — значит последняя попытка не удалась (не было сети).
        assertFalse(ScheduleWidgetRefresh.isDue(t + 60_000, t, t - 100_000));
        assertTrue(ScheduleWidgetRefresh.isDue(t + ScheduleWidgetRefresh.RETRY_INTERVAL_MS, t, t - 100_000));
    }

    @Test
    public void clockMovedBackwardsDoesNotLockUpdatesForever() {
        //Часы перевели назад (или сдвинул NTP). Наивное «прошло меньше интервала» дало бы
        //отрицательную разницу и заперло бы обновления до тех пор, пока время не догонит.
        //Ровно этот баг уже ловили в бэкапах десктопа — второй раз наступать не будем.
        assertTrue(ScheduleWidgetRefresh.isDue(1_000L, 9_000_000_000L, 9_000_000_000L));
    }

    // ───────────────────────── адрес ─────────────────────────

    @Test
    public void groupUrlEncodesSlashInGroupName() throws Exception {
        JSONObject snap = new JSONObject()
                .put("target", new JSONObject().put("kind", "group").put("name", "К74/1").put("category", ""));
        String url = ScheduleWidgetRefresh.urlFor("https://esstu-gradebook.ru/", snap);
        assertNotNull(url);
        //Слэш в названии группы («К74/1») — старая мина проекта: незакодированным он
        //разваливает путь на лишний сегмент. Здесь он в query, но кодировать обязаны всё
        //равно: иначе параметр обрывается на слэше.
        assertTrue(url, url.startsWith("https://esstu-gradebook.ru/public/schedule?group="));
        assertTrue("слэш обязан быть закодирован: " + url, url.contains("%2F"));
        assertFalse("двойного слэша быть не должно: " + url, url.contains(".ru//public"));
    }

    @Test
    public void teacherUrlUsesItsOwnPath() throws Exception {
        JSONObject snap = new JSONObject()
                .put("target", new JSONObject().put("kind", "teacher").put("name", "Иванов И.И.")
                        .put("category", "bakalavriat"));
        String url = ScheduleWidgetRefresh.urlFor("https://esstu-gradebook.ru", snap);
        assertNotNull(url);
        assertTrue(url, url.startsWith("https://esstu-gradebook.ru/public/schedule/teacher?name="));
        assertTrue("категория обязана уехать в запрос: " + url, url.contains("&category=bakalavriat"));
    }

    @Test
    public void withoutTargetWeDoNotGuess() throws Exception {
        //Снимок положен старой версией приложения, где цели ещё не было. Угадывать группу
        //нельзя: покажем человеку чужие пары, и он не заметит подмены.
        assertNull(ScheduleWidgetRefresh.urlFor("https://esstu-gradebook.ru", new JSONObject()));
        assertNull(ScheduleWidgetRefresh.urlFor("https://esstu-gradebook.ru",
                new JSONObject().put("target", new JSONObject().put("kind", "group").put("name", ""))));
    }

    @Test
    public void insecureEndpointYieldsNoUrlAtAll() throws Exception {
        JSONObject snap = new JSONObject()
                .put("target", new JSONObject().put("kind", "group").put("name", "К74/1"));
        assertNull(ScheduleWidgetRefresh.urlFor("http://192.168.1.10:8000", snap));
    }

    // ───────────────────────── перевод ответа ─────────────────────────

    private static JSONObject groupResponse() throws Exception {
        JSONObject lesson = new JSONObject()
                .put("pair_no", 2).put("time", "10:45-12:20").put("subject", "Компьютерные сети")
                .put("kind", "лек").put("room", "301").put("teacher", "Иванов И.И.");
        return new JSONObject()
                .put("available", true)
                .put("schedule", new JSONObject().put("weeks", new JSONObject()
                        .put("1", new JSONObject().put("Пнд", new org.json.JSONArray().put(lesson)))));
    }

    private static JSONObject prevSnapshot(String kind, String name) throws Exception {
        return new JSONObject()
                .put("title", name)
                .put("target", new JSONObject().put("kind", kind).put("name", name).put("category", ""));
    }

    @Test
    public void groupResponseBecomesFlatDayKeys() throws Exception {
        JSONObject snap = ScheduleWidgetRefresh.snapshotFrom(groupResponse(), prevSnapshot("group", "К74/1"));
        assertNotNull(snap);
        assertEquals("weekly", snap.optString("kind"));
        //Ключ дня плоский — «чётность|день», как его собирает веб-слой и ищет
        //ScheduleWidgetData.dayKey. Второго формата в продукте нет.
        JSONObject day = snap.optJSONObject("days");
        assertNotNull(day);
        assertNotNull("день обязан лежать под ключом 1|Пнд", day.optJSONArray("1|Пнд"));
        JSONObject row = day.optJSONArray("1|Пнд").optJSONObject(0);
        assertEquals(2, row.optInt("n"));
        assertEquals("Компьютерные сети", row.optString("s"));
        assertEquals("301", row.optString("r"));
        assertEquals("у студента в поле who — преподаватель", "Иванов И.И.", row.optString("w"));
    }

    @Test
    public void titleAndTargetSurviveTheRefresh() throws Exception {
        JSONObject snap = ScheduleWidgetRefresh.snapshotFrom(groupResponse(), prevSnapshot("group", "К74/1"));
        assertNotNull(snap);
        //Публичный эндпоинт не знает, ЧЬЁ это расписание, — он лишь отвечает на заданный
        //вопрос. Потеряй мы цель, следующее самообновление стало бы невозможным.
        assertEquals("К74/1", snap.optString("title"));
        assertEquals("К74/1", snap.optJSONObject("target").optString("name"));
    }

    @Test
    public void teacherResponseUnwrapsLessonAndPutsGroupIntoWho() throws Exception {
        JSONObject lesson = new JSONObject()
                .put("pair_no", 1).put("time", "08:00-09:35").put("subject", "Физика").put("room", "12");
        JSONObject entry = new JSONObject().put("group", "К74/1").put("lesson", lesson);
        JSONObject resp = new JSONObject()
                .put("available", true)
                .put("schedule", new JSONObject().put("weeks", new JSONObject()
                        .put("2", new JSONObject().put("Втр", new org.json.JSONArray().put(entry)))));

        JSONObject snap = ScheduleWidgetRefresh.snapshotFrom(resp, prevSnapshot("teacher", "Петров П.П."));
        assertNotNull(snap);
        JSONObject row = snap.optJSONObject("days").optJSONArray("2|Втр").optJSONObject(0);
        //У преподавателя вопрос «к кому я иду», а не «кто ведёт»: в поле who обязана
        //стоять ГРУППА. Без разворачивания обёртки предмет остался бы пустым и пара
        //молча исчезла бы из виджета.
        assertEquals("Физика", row.optString("s"));
        assertEquals("К74/1", row.optString("w"));
    }

    @Test
    public void nothingToShowKeepsTheOldSnapshot() throws Exception {
        JSONObject prev = prevSnapshot("group", "К74/1");
        //Сервер честно сказал «недоступно» (портал не ответил, группа не найдена).
        assertNull(ScheduleWidgetRefresh.snapshotFrom(new JSONObject().put("available", false), prev));
        //Индекс преподавателей ещё строится — расписания нет, но и ошибки нет.
        assertNull(ScheduleWidgetRefresh.snapshotFrom(
                new JSONObject().put("available", true).put("building", true), prev));
        //⚠️ ЗДЕСЬ СТОЯЛА ТРЕТЬЯ ПРОВЕРКА — «дни есть, но все пустые → null», с пояснением
        //«устаревшее расписание полезнее, чем „пар нет“ посреди учебной недели». Она
        //ПЕРЕЕХАЛА в отдельный тест ниже С ПРОТИВОПОЛОЖНЫМ ожиданием, и это не подгонка
        //ожидания под код, а отмена самого решения — см. emptyScheduleIsAnAnswerNotAFailure.
    }

    /**
     * 🔥 ПУСТОЕ РАСПИСАНИЕ — ЭТО ОТВЕТ, А НЕ ОТКАЗ (07.09.2026).
     *
     * Прежде `snapshotFrom` возвращал null и на непригодный ответ, и на корректный ответ
     * с нулём пар. Оба случая оставляли на экране ПРОШЛЫЙ снимок. Для первого это верно
     * («не знаем — не трогаем»), для второго — нет: каникулы, отменённые занятия и
     * очищенное расписание давали виджет, показывающий пары, которых нет. Студент
     * собирался на занятие по нашему виджету.
     *
     * Разница между случаями измеримая, а не оценочная: «ответ повреждён» — это когда
     * нет `available` или нет тела `weeks`; «пар нет» — когда тело есть и оно пустое.
     * Обе проверки стоят ВЫШЕ по коду и уже отсеяли всё, о чём судить нельзя.
     */
    @Test
    public void emptyScheduleIsAnAnswerNotAFailure() throws Exception {
        JSONObject prev = prevSnapshot("group", "К74/1");
        JSONObject empty = new JSONObject()
                .put("available", true)
                .put("schedule", new JSONObject().put("weeks", new JSONObject()
                        .put("1", new JSONObject().put("Пнд", new org.json.JSONArray()))));

        JSONObject snap = ScheduleWidgetRefresh.snapshotFrom(empty, prev);
        assertNotNull("корректный ответ «пар нет» обязан обновить снимок", snap);
        assertEquals(0, snap.optJSONObject("days").length());
        assertTrue("снимок обязан помечать себя пустым ПО ДАННЫМ, а не по неудаче",
                snap.optBoolean("empty", false));
        //Заголовок и цель берём из прежнего снимка — публичная ручка их не знает.
        assertEquals("К74/1", snap.optJSONObject("target").optString("name"));
    }

    @Test
    public void aBrokenResponseStillKeepsTheOldSnapshot() throws Exception {
        JSONObject prev = prevSnapshot("group", "К74/1");
        //Тела расписания нет вовсе — вот это и есть «не знаем». Здесь прежнее решение
        //остаётся в силе: устаревшее лучше пустого, потому что правды у нас нет.
        assertNull(ScheduleWidgetRefresh.snapshotFrom(
                new JSONObject().put("available", true), prev));
        assertNull(ScheduleWidgetRefresh.snapshotFrom(
                new JSONObject().put("available", true).put("schedule", new JSONObject()), prev));
    }

    @Test
    public void aRealScheduleIsNotMarkedEmpty() throws Exception {
        JSONObject snap = ScheduleWidgetRefresh.snapshotFrom(
                groupResponse(), prevSnapshot("group", "К74/1"));
        assertNotNull(snap);
        assertFalse("непустое расписание не имеет права помечаться как «пар нет»",
                snap.optBoolean("empty", false));
    }

    @Test
    public void emptyCellsAreSkippedButRealPairsSurvive() throws Exception {
        org.json.JSONArray day = new org.json.JSONArray()
                .put(new JSONObject().put("pair_no", 1).put("subject", "  "))       //пустая клетка портала
                .put(new JSONObject().put("pair_no", 3).put("raw", "Химия"));       //предмет только в raw
        JSONObject resp = new JSONObject()
                .put("available", true)
                .put("schedule", new JSONObject().put("weeks", new JSONObject()
                        .put("1", new JSONObject().put("Срд", day))));
        JSONObject snap = ScheduleWidgetRefresh.snapshotFrom(resp, prevSnapshot("group", "К74/1"));
        assertNotNull(snap);
        org.json.JSONArray rows = snap.optJSONObject("days").optJSONArray("1|Срд");
        assertEquals("пустая клетка — не пара", 1, rows.length());
        assertEquals("Химия", rows.optJSONObject(0).optString("s"));
    }
}
