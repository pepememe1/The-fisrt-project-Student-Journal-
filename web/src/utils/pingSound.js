/**
 * pingSound.js — короткий звуковой сигнал ГРОМКОЙ отметки (`/@!Фамилия`).
 *
 * Почему синтез, а не mp3-файл: сигнал нужен один и очень короткий, а лишний бинарник —
 * это ещё один ассет в вебе, в OTA-бандле мобилки и в .exe (там всё считаем, см. §11).
 *
 * 🔥 ЗДЕСЬ БЫЛ СВОЙ ЗВУКОВОЙ КОНТЕКСТ, И ЕГО НЕ БУДИЛ НИКТО (починено 07.09.2026 по
 * жалобе «музыка и некоторые звуки на телефонах не слышно»). В шапке стояло дословно:
 * «Отдельная разблокировка тут не нужна — тот же AudioContext „будит" вход в приложение
 * (см. primeAudio в stores/vector.js)». Контекст был ДРУГОЙ: там будится контекст
 * озвучки (`stores/tts.js`), а тут жила отдельная переменная `_ctx`. Обещание в
 * комментарии не является механизмом — теперь контекст РЕАЛЬНО общий, см.
 * `utils/audioContext.js`.
 */
import { withAudio } from './audioContext'

/** Две короткие ноты вверх — узнаваемо «вас позвали», но не тревожно. */
export function playMentionPing() {
  withAudio((ctx, now) => {
    // 880 Гц → 1320 Гц: интервал слышен даже на слабых динамиках ноутбука.
    for (const [freq, at] of [[880, 0], [1320, 0.13]]) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      // Плавное затухание вместо резкого стопа — иначе слышен щелчок обрыва волны.
      gain.gain.setValueAtTime(0.0001, now + at)
      gain.gain.exponentialRampToValueAtTime(0.18, now + at + 0.01)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.12)
      osc.connect(gain).connect(ctx.destination)
      osc.start(now + at)
      osc.stop(now + at + 0.13)
    }
  })
}
