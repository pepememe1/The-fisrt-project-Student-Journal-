package ru.esstu.gradebook;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.Iterator;

/**
 * Самообновление виджета: сходить за свежим расписанием БЕЗ токена и без приложения.
 *
 * ━━ ЗАЧЕМ ЭТО ВООБЩЕ ━━
 * Снимок кладёт приложение в момент входа (см. ScheduleWidgetData и мост в MainActivity).
 * Этого достаточно, чтобы виджет был верным долго — расписание портала недельное, а
 * «какой сегодня день» и «какая пара идёт» считаются локально. Но у самого приложения
 * человек может не появляться неделями, а расписание за это время правят: заменили пару,
 * админ наложил `ScheduleOverride`. Тогда виджет уверенно показывает НЕ ТО — а
 * правдоподобная неправда хуже пустоты, потому что её не перепроверяют.
 *
 * ━━ ПОЧЕМУ БЕЗ ТОКЕНА ━━
 * JWT в проекте живёт жёстко 5 часов (§6 доки команды), после чего нужен повторный вход.
 * Виджет, работающий первые пять часов после входа, — хуже отсутствующего. Поэтому ходим
 * в ПУБЛИЧНЫЙ `/public/schedule*` (server/app/routers/publicschedule.py): расписание и
 * так открыто опубликовано вузом на portal.esstu.ru, новых данных мы не раскрываем.
 *
 * ⚠️ ТОЛЬКО HTTPS. Та же заслонка и по той же причине, что в автообновлении десктопа:
 * по открытому каналу ответ подменяет любой, кто встал в середину, — и человек приходит
 * не на ту пару, не в ту аудиторию. Проверять подпись расписания нечем (оно приходит тем
 * же каналом), поэтому единственная защита — сам канал. В манифесте приложения
 * `usesCleartextTraffic="true"` (он нужен локальному бандлу Capacitor), значит без явного
 * отказа запрос по http прошёл бы молча.
 *
 * ⚠️ ЧТО ЗДЕСЬ НИКОГДА НЕ ПОЯВИТСЯ: оценки, пропуски, долги, ЗЕТ. Виджет висит на
 * рабочем столе, который видят посторонние, а публичный эндпоинт по построению не отдаёт
 * ничего из журнала.
 */
final class ScheduleWidgetRefresh {

    /** Базовый адрес сервера — его кладёт веб-слой (scheduleWidget.js::saveEndpoint). */
    static final String KEY_ENDPOINT = "endpoint";
    /** Когда последний раз ПЫТАЛИСЬ сходить (не «успешно сходили»). */
    static final String KEY_LAST_TRY = "last_try";
    /** Когда последний раз успешно обновились — показывается в подвале виджета. */
    static final String KEY_LAST_OK = "last_ok";

    /**
     * Как часто ходить в сеть.
     *
     * Шесть часов — не «на всякий случай», а соответствие данным: расписание на семестр
     * меняется единицами раз, и чаще ходить незачем. Система и так будит виджет не чаще
     * чем раз в 30 минут (updatePeriodMillis), поэтому без этого предела мы дёргали бы
     * парсер портала 48 раз в сутки с каждого телефона колледжа — и упёрлись бы в
     * собственный же лимит частоты на сервере.
     */
    static final long MIN_INTERVAL_MS = 6L * 60 * 60 * 1000;
    /** После неудачи повторяем раньше: сеть могла просто отсутствовать в тот момент. */
    static final long RETRY_INTERVAL_MS = 30L * 60 * 1000;

    private static final int CONNECT_TIMEOUT_MS = 6000;
    private static final int READ_TIMEOUT_MS = 8000;
    /** Потолок ответа. Расписание группы — десятки килобайт; мегабайт означает, что мы
     *  разговариваем не с тем сервером, и разбирать это не нужно. */
    private static final int MAX_BODY_BYTES = 2 * 1024 * 1024;

    private ScheduleWidgetRefresh() {
    }

    /** True — пора сходить за свежим расписанием. Вынесено отдельно ради теста. */
    static boolean isDue(long now, long lastTry, long lastOk) {
        if (lastTry <= 0) {
            return true;                       //ещё ни разу не ходили
        }
        if (now < lastTry) {
            //Часы перевели назад. Сравнение «прошло меньше интервала» дало бы отрицательную
            //разницу и заперло бы обновления до тех пор, пока время не догонит — ровно тот
            //баг, что уже ловили в бэкапах десктопа (backup_if_due).
            return true;
        }
        long since = now - lastTry;
        boolean lastFailed = lastOk <= 0 || lastOk < lastTry;
        return since >= (lastFailed ? RETRY_INTERVAL_MS : MIN_INTERVAL_MS);
    }

    /**
     * Адрес пригоден для запроса.
     *
     * Пусто — веб-слой ещё не сообщил сервер (старый APK или человек не входил).
     * Не https — отказ, см. заслонку в шапке файла.
     */
    static boolean transportOk(String base) {
        if (base == null) {
            return false;
        }
        String b = base.trim();
        return b.length() > 8 && b.toLowerCase(java.util.Locale.US).startsWith("https://");
    }

    /** Куда идти за обновлением ЭТОГО снимка; null — цель неизвестна, обновлять нечего. */
    static String urlFor(String base, JSONObject snap) {
        if (!transportOk(base) || snap == null) {
            return null;
        }
        JSONObject target = snap.optJSONObject("target");
        if (target == null) {
            //Снимок положен старой версией приложения, где цели ещё не было. Сами
            //угадывать группу не имеем права: покажем чужие пары. Обновится при
            //следующем входе, когда веб-слой перепишет снимок вместе с целью.
            return null;
        }
        String kind = target.optString("kind", "");
        String name = target.optString("name", "");
        String category = target.optString("category", "");
        if (name.isEmpty()) {
            return null;
        }
        String root = base.trim();
        while (root.endsWith("/")) {
            root = root.substring(0, root.length() - 1);
        }
        String path = "teacher".equals(kind) ? "/public/schedule/teacher?name=" : "/public/schedule?group=";
        return root + path + enc(name) + (category.isEmpty() ? "" : "&category=" + enc(category));
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * Ответ `/public/schedule*` → снимок в формате виджета.
     *
     * Форма ответа НАМЕРЕННО совпадает с `/web/schedule*`, поэтому здесь ровно тот же
     * разбор, что в web/src/services/scheduleWidget.js: `weeks[чётность][день]` → плоский
     * ключ «1|Пнд». Третьего формата не заводим — он разошёлся бы с первыми двумя.
     *
     * Возвращает null, только если ответ НЕПРИГОДЕН: сервер сказал `available:false`,
     * индекс преподавателей ещё строится, тела расписания нет вовсе. Тогда старый снимок
     * остаётся на месте — устаревший виджет лучше пустого.
     *
     * 🔥 А ВОТ ПУСТОЕ РАСПИСАНИЕ — ЭТО ОТВЕТ, А НЕ ОТКАЗ (07.09.2026).
     * Прежде обе ситуации сводились в один `return null`, и комментарий объяснял это как
     * решение. Решение было неверным: «ответ повреждён» и «пар действительно нет» —
     * разные события, и во втором случае мы ЗНАЕМ правду. Каникулы, отменённые занятия,
     * очищенное расписание — сервер честно отдаёт корректный ответ с нулём пар, а виджет
     * продолжал показывать прошлую неделю. Студент собирался на пару, которой нет.
     * Устаревшее лучше пустого ровно тогда, когда мы не знаем; когда знаем — наоборот.
     */
    static JSONObject snapshotFrom(JSONObject resp, JSONObject prev) {
        if (resp == null || prev == null || !resp.optBoolean("available", false)) {
            return null;
        }
        JSONObject schedule = resp.optJSONObject("schedule");
        JSONObject weeks = schedule != null ? schedule.optJSONObject("weeks") : null;
        if (weeks == null) {
            return null;
        }
        boolean teacher = "teacher".equals(prev.optJSONObject("target") != null
                ? prev.optJSONObject("target").optString("kind", "") : "");
        JSONObject days = new JSONObject();
        int filled = 0;
        for (Iterator<String> wk = weeks.keys(); wk.hasNext(); ) {
            String week = wk.next();
            JSONObject byDay = weeks.optJSONObject(week);
            if (byDay == null) {
                continue;
            }
            for (Iterator<String> dk = byDay.keys(); dk.hasNext(); ) {
                String day = dk.next();
                JSONArray rows = packDay(byDay.optJSONArray(day), teacher);
                if (rows.length() > 0) {
                    try {
                        days.put(week + "|" + day, rows);
                        filled++;
                    } catch (Exception ignored) {
                        //Ключ строковый и непустой — сюда попасть нечем, но падать из-за
                        //виджета нельзя ни при каких данных с чужого сайта.
                    }
                }
            }
        }
        //⚠️ Ноль пар при ИСПРАВНОМ теле расписания — законный ответ, и мы его сохраняем.
        //Проверки «ответ пригоден» стоят выше (available, наличие weeks) и уже отсеяли
        //всё, о чём нельзя судить. Здесь остаётся ровно один случай: пар нет.
        JSONObject out = new JSONObject();
        try {
            out.put("kind", "weekly");
            //Заголовок и цель берём из ПРЕЖНЕГО снимка: публичный эндпоинт не знает, чьё
            //это расписание, — он лишь отвечает на заданный вопрос.
            out.put("title", prev.optString("title", ""));
            out.put("target", prev.optJSONObject("target"));
            out.put("days", days);
            out.put("saved_at", System.currentTimeMillis());
            //Признак «расписание пустое ПО ДАННЫМ, а не потому что мы не смогли». По нему
            //виджет рисует «Пар нет» вместо прошлой недели, а не гадает по пустому days.
            out.put("empty", filled == 0);
        } catch (Exception e) {
            return null;
        }
        return out;
    }

    /**
     * Один день → компактные записи, как в packLesson веб-слоя.
     *
     * У преподавателя элемент дня — обёртка {group, lesson}: в его вопросе «к кому я
     * иду», а не «кто ведёт», поэтому в поле `w` уезжает ГРУППА.
     */
    private static JSONArray packDay(JSONArray src, boolean teacher) {
        JSONArray out = new JSONArray();
        if (src == null) {
            return out;
        }
        for (int i = 0; i < src.length(); i++) {
            JSONObject item = src.optJSONObject(i);
            if (item == null) {
                continue;
            }
            JSONObject ls = item;
            String who = "";
            if (teacher) {
                JSONObject inner = item.optJSONObject("lesson");
                if (inner != null) {
                    ls = inner;
                }
                who = item.optString("group", "");
            } else {
                who = ls.optString("teacher", "");
            }
            String subject = ls.optString("subject", "");
            if (subject.isEmpty()) {
                subject = ls.optString("raw", "");
            }
            subject = subject.trim();
            if (subject.isEmpty()) {
                continue;              //пустая клетка портала — не пара
            }
            JSONObject row = new JSONObject();
            try {
                row.put("n", ls.optInt("pair_no", i + 1));
                row.put("t", ls.optString("time", ""));
                row.put("s", subject);
                row.put("k", ls.optString("kind", ""));
                row.put("r", ls.optString("room", ""));
                row.put("w", who);
            } catch (Exception e) {
                continue;
            }
            out.put(row);
        }
        return out;
    }

    /**
     * Сходить и обновить снимок. Блокирующая — звать ТОЛЬКО с фонового потока.
     *
     * Возвращает true, если снимок реально сменился (значит, виджеты надо перерисовать).
     * Любая ошибка — это просто false: виджет остаётся на прошлых данных, а исключение
     * из-за виджета не имеет права уронить процесс приложения.
     */
    /**
     * 🔥 ОДНО ОБНОВЛЕНИЕ ЗА РАЗ (07.09.2026).
     *
     * Приёмников виджета зарегистрировано несколько (разные размеры), и системные события
     * приходят пачками. Проверка «пора ли» и запись времени попытки шли двумя отдельными
     * действиями, между которыми успевал вклиниться соседний поток: оба проходили проверку,
     * оба лезли в сеть, оба перерисовывали виджеты. На телефоне это лишний трафик и лишние
     * пробуждения — ровно то, за что приложение и ругают за расход батареи.
     *
     * Флаг процесса, а не файловый: приёмники живут в ОДНОМ процессе приложения, а
     * переживать его перезапуск этой блокировке не нужно и вредно (упавший процесс
     * оставил бы виджет заблокированным навсегда).
     */
    private static final java.util.concurrent.atomic.AtomicBoolean BUSY =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    static boolean refreshNow(Context ctx) {
        if (!BUSY.compareAndSet(false, true)) {
            //Кто-то уже качает то же самое расписание. Второй запрос ничего не добавит.
            return false;
        }
        try {
            return refreshLocked(ctx);
        } finally {
            BUSY.set(false);
        }
    }

    private static boolean refreshLocked(Context ctx) {
        SharedPreferences prefs = ScheduleWidgetData.prefs(ctx);
        long now = System.currentTimeMillis();
        if (!isDue(now, prefs.getLong(KEY_LAST_TRY, 0), prefs.getLong(KEY_LAST_OK, 0))) {
            return false;
        }
        JSONObject prev = ScheduleWidgetData.snapshot(ctx);
        String url = urlFor(prefs.getString(KEY_ENDPOINT, ""), prev);
        if (url == null) {
            return false;
        }
        //Метку попытки ставим ДО запроса: иначе при стабильно падающей сети мы ходили бы
        //в неё на каждом пробуждении виджета.
        prefs.edit().putLong(KEY_LAST_TRY, now).apply();

        String body = fetch(url);
        if (body == null) {
            return false;
        }
        JSONObject fresh;
        try {
            fresh = snapshotFrom(new JSONObject(body), prev);
        } catch (Exception e) {
            return false;
        }
        if (fresh == null) {
            return false;
        }
        String next = fresh.toString();
        prefs.edit()
                .putString(ScheduleWidgetData.KEY_SNAPSHOT, next)
                .putLong(KEY_LAST_OK, now)
                .apply();
        return true;
    }

    /** GET с жёсткими таймаутами. null — не получилось (и это нормальный исход). */
    private static String fetch(String url) {
        HttpURLConnection conn = null;
        try {
            URL u = new URL(url);
            if (!"https".equalsIgnoreCase(u.getProtocol())) {
                return null;         //вторая проверка: адрес мог прийти уже собранным
            }
            conn = (HttpURLConnection) u.openConnection();
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setRequestProperty("Accept", "application/json");
            //Клиент подписывается: на сервере видно, что это виджет, а не браузер, и
            //его запросы можно отделить в логах и в лимите частоты.
            conn.setRequestProperty("X-Client", "android-widget");
            conn.setInstanceFollowRedirects(false);   //редирект с https на http — не наш путь
            if (conn.getResponseCode() != 200) {
                return null;                          //429 «слишком часто» сюда же: просто ждём
            }
            InputStream in = conn.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            int total = 0;
            while ((n = in.read(chunk)) > 0) {
                total += n;
                if (total > MAX_BODY_BYTES) {
                    return null;
                }
                buf.write(chunk, 0, n);
            }
            return buf.toString("UTF-8");
        } catch (Throwable e) {
            return null;
        } finally {
            if (conn != null) {
                try {
                    conn.disconnect();
                } catch (Throwable ignored) {
                    //disconnect у уже оборванного соединения ничего не значит
                }
            }
        }
    }
}
