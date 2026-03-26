<?php
/**
 * Plugin Name: SkyEd Lesson Renderer Next
 * Description: Renders SkyEd-generated lesson payload data (cards + audio + practice) via shortcode.
 * Version: 0.4.4
 * Author: Sky Education
 */

if (!defined('ABSPATH')) { exit; }

class SkyEd_Lesson_Renderer {
    const SHORTCODE = 'skyed_lesson';
    const CSS_HANDLE = 'skyed-lesson-renderer-next';
    const SHOW_PUBLIC_CATEGORIES = false;
    const SHOW_PUBLIC_PRON_WIDGET = false;
    const SHOW_PUBLIC_TAG_GAMES = true;

    public static function init() : void {
        add_action('wp_enqueue_scripts', [__CLASS__, 'enqueue_assets']);
        add_shortcode(self::SHORTCODE, [__CLASS__, 'shortcode']);
        add_filter('body_class', [__CLASS__, 'body_class']);
        add_filter('upload_mimes', [__CLASS__, 'allow_payload_mimes'], 10, 2);
        add_filter('wp_check_filetype_and_ext', [__CLASS__, 'fix_payload_filetype'], 10, 5);
        // Pronunciation launcher is preserved in code but intentionally not mounted on public pages for now.
    }

    public static function allow_payload_mimes($mimes, $user) {
        if (!is_array($mimes)) {
            $mimes = [];
        }
        $mimes['json'] = 'application/json';
        $mimes['txt'] = 'text/plain';
        return $mimes;
    }

    public static function fix_payload_filetype($data, $file, $filename, $mimes, $real_mime) {
        $ext = strtolower(pathinfo((string)$filename, PATHINFO_EXTENSION));
        if ($ext === 'json') {
            return ['ext' => 'json', 'type' => 'application/json', 'proper_filename' => $filename];
        }
        if ($ext === 'txt') {
            return ['ext' => 'txt', 'type' => 'text/plain', 'proper_filename' => $filename];
        }
        return $data;
    }

    public static function body_class($classes) {
        if (!is_singular()) {
            return $classes;
        }
        global $post;
        if ($post && isset($post->post_content) && has_shortcode((string)$post->post_content, self::SHORTCODE)) {
            $classes[] = 'skyed-lesson-page';
        }
        return $classes;
    }

    public static function enqueue_assets() : void {
        wp_register_style(
            self::CSS_HANDLE,
            plugins_url('assets/skyed-lesson.css', __FILE__),
            [],
            '0.4.4'
        );
        wp_enqueue_style(self::CSS_HANDLE);
    }

    private static function fetch_payload(string $url) : array {
        $url = trim($url);
        if ($url === '') {
            return [];
        }
        $cache_key = 'skyed_payload_' . md5($url);
        $cached = get_transient($cache_key);
        if (is_array($cached) && !empty($cached)) {
            return $cached;
        }
        $resp = wp_remote_get($url, [
            'timeout'     => 15,
            'redirection' => 3,
            'headers'     => ['Accept' => 'application/json, text/plain, */*']
        ]);
        if (is_wp_error($resp)) {
            return [];
        }
        $code = wp_remote_retrieve_response_code($resp);
        $body = wp_remote_retrieve_body($resp);
        if ($code < 200 || $code >= 300 || !is_string($body) || $body === '') {
            return [];
        }
        $data = json_decode($body, true);
        if (!is_array($data)) {
            return [];
        }
        set_transient($cache_key, $data, 10 * MINUTE_IN_SECONDS);
        return $data;
    }

    private static function esc($s) : string {
        return esc_html((string)$s);
    }

    private static function escu($s) : string {
        return esc_url((string)$s);
    }

    private static function pronunciation_endpoint() : string {
        if (defined('SKYED_PRON_ENDPOINT')) {
            return trim((string)constant('SKYED_PRON_ENDPOINT'));
        }
        return '';
    }

    public static function render_pronunciation_launcher() : void {
        if (!self::SHOW_PUBLIC_PRON_WIDGET) {
            return;
        }
        $endpoint = self::pronunciation_endpoint();
        if ($endpoint === '') {
            return;
        }
        ?>
        <div class="skyed-pron" data-endpoint="<?php echo esc_attr($endpoint); ?>">
          <button class="skyed-pron__fab" type="button">🎙 Pronunciation</button>
          <div class="skyed-pron__panel" hidden>
            <div class="skyed-pron__title">Pronunciation checker</div>
            <textarea class="skyed-pron__text" placeholder="Type the word or sentence to practise"></textarea>
            <div class="skyed-pron__actions">
              <button class="skyed-pron__btn skyed-pron__btn--record" type="button">Start recording</button>
              <button class="skyed-pron__btn skyed-pron__btn--close" type="button">Close</button>
            </div>
            <div class="skyed-pron__result">Ready.</div>
          </div>
        </div>
        <script>
        (function(){
          const root = document.querySelector('.skyed-pron[data-endpoint]');
          if (!root || root.dataset.bound === '1') return;
          root.dataset.bound = '1';
          const panel = root.querySelector('.skyed-pron__panel');
          const fab = root.querySelector('.skyed-pron__fab');
          const btnRecord = root.querySelector('.skyed-pron__btn--record');
          const btnClose = root.querySelector('.skyed-pron__btn--close');
          const textEl = root.querySelector('.skyed-pron__text');
          const resultEl = root.querySelector('.skyed-pron__result');
          const endpoint = root.getAttribute('data-endpoint');
          let mediaRecorder = null;
          let chunks = [];
          let stream = null;
          let isRecording = false;

          fab.addEventListener('click', function(){ panel.hidden = !panel.hidden; });
          btnClose.addEventListener('click', function(){ panel.hidden = true; });

          btnRecord.addEventListener('click', async function(){
            if (!endpoint) return;
            if (!textEl.value.trim()) { resultEl.textContent = 'Type the target sentence first.'; return; }
            if (!isRecording) {
              try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                chunks = [];
                mediaRecorder = new MediaRecorder(stream);
                mediaRecorder.ondataavailable = function(e){ if (e.data && e.data.size) chunks.push(e.data); };
                mediaRecorder.onstop = async function(){
                  const blob = new Blob(chunks, { type: 'audio/webm' });
                  const fd = new FormData();
                  fd.append('audio', blob, 'speech.webm');
                  fd.append('expected_text', textEl.value.trim());
                  resultEl.textContent = 'Scoring...';
                  try {
                    const resp = await fetch(endpoint, { method: 'POST', body: fd });
                    const data = await resp.json();
                    resultEl.textContent = 'Score: ' + (data.score ?? 'n/a') + ' | Heard: ' + (data.transcript ?? '');
                  } catch (e) {
                    resultEl.textContent = 'Pronunciation service error.';
                  }
                  if (stream) { stream.getTracks().forEach(t => t.stop()); }
                  stream = null;
                };
                mediaRecorder.start();
                isRecording = true;
                btnRecord.textContent = 'Stop recording';
                resultEl.textContent = 'Recording...';
              } catch (e) {
                resultEl.textContent = 'Microphone permission failed.';
              }
            } else {
              isRecording = false;
              btnRecord.textContent = 'Start recording';
              if (mediaRecorder) mediaRecorder.stop();
            }
          });
        })();
        </script>
        <?php
    }

    private static function theme_name(string $theme) : string {
        $theme = strtolower(trim($theme));
        $aliases = [
            'app' => 'sky',
            'sky' => 'sky',
            'sky_tiles' => 'sky_tiles',
            'strict' => 'strict_dark',
            'strict_dark' => 'strict_dark',
            'fun' => 'fun_mission',
            'fun_mission' => 'fun_mission',
            'ng' => 'ng',
        ];
        return $aliases[$theme] ?? 'sky';
    }

    private static function stat_chip(string $text) : string {
        return '<span class="skyed-chip">' . esc_html($text) . '</span>';
    }

    private static function theme_copy(string $theme) : array {
        switch ($theme) {
            case 'sky_tiles':
                return [
                    'kicker' => 'Sky Tiles',
                    'subtitle' => 'Tap → listen → say',
                    'vocab_note' => 'Big picture tiles for pre-readers. Tap a tile to hear the word.',
                    'sent_note' => 'Listen and say it aloud. No reading is required here.',
                    'practice_note' => 'Listen carefully and tap the matching picture only.',
                ];
            case 'strict_dark':
                return [
                    'kicker' => 'Study Mode',
                    'subtitle' => 'Words → usage → study check',
                    'vocab_note' => 'Read the word, hear it once, then use it carefully.',
                    'sent_note' => 'Focus on sentence meaning and correct usage.',
                    'practice_note' => 'Text-first checks with a calmer study rhythm.',
                ];
            case 'fun_mission':
                return [
                    'kicker' => 'Mission Mode',
                    'subtitle' => 'Warm-up → checkpoints → mission check',
                    'vocab_note' => 'Take one small step at a time and clear each checkpoint.',
                    'sent_note' => 'Say each sentence, then move to the next mission step.',
                    'practice_note' => 'One question at a time with clear actions and progress.',
                ];
            case 'ng':
                return [
                    'kicker' => 'NG Lesson',
                    'subtitle' => 'Touch → listen → happy practice',
                    'vocab_note' => 'Touch-and-listen support is added as a tag_s block for this lesson.',
                    'sent_note' => 'Keep sentence practice short, clear, and repeatable.',
                    'practice_note' => 'Happy Practice audio appears only for the files uploaded for this lesson.',
                ];
            default:
                return [
                    'kicker' => 'Sky Education',
                    'subtitle' => 'Vocabulary → sentence practice → practice',
                    'vocab_note' => 'Listen, repeat, then use the word in one full sentence.',
                    'sent_note' => 'Short, clear sentence practice for today’s lesson.',
                    'practice_note' => 'Short, logical checks based on today’s lesson only.',
                ];
        }
    }

    private static function render_vocab_card(array $it, string $theme, int $idx) : string {
        $en   = isset($it['en']) ? (string)$it['en'] : '';
        $zh   = isset($it['zh']) ? (string)$it['zh'] : '';
        $img  = isset($it['img']) ? (string)$it['img'] : '';
        $a_en = isset($it['audio_en']) ? (string)$it['audio_en'] : '';
        $a_zh = isset($it['audio_zh']) ? (string)$it['audio_zh'] : '';
        $pos  = isset($it['pos']) ? strtoupper((string)$it['pos']) : '';

        ob_start();
        if ($theme === 'sky_tiles'): ?>
            <article class="skyed-card skyed-card--tile" data-audio-en="<?php echo esc_attr($a_en); ?>" data-audio-zh="<?php echo esc_attr($a_zh); ?>">
              <button class="skyed-tile-media" type="button" <?php echo $a_en ? 'data-play="en"' : ''; ?> aria-label="Play <?php echo esc_attr($en); ?>">
                <span class="skyed-tile-media__frame">
                <?php if ($img !== ''): ?>
                  <img src="<?php echo self::escu($img); ?>" alt="<?php echo esc_attr($en); ?>" loading="lazy">
                <?php else: ?>
                  <span class="skyed-card__missing"><span>No image</span></span>
                <?php endif; ?>
                </span>
                <span class="skyed-play-badge">▶</span>
              </button>
              <div class="skyed-card__body skyed-card__body--tile">
                <div class="skyed-tile-word"><?php echo self::esc($en); ?></div>
                <?php if ($zh !== ''): ?><div class="skyed-tile-zh"><?php echo self::esc($zh); ?></div><?php endif; ?>
                <div class="skyed-tile-caption">Tap to hear, then say it</div>
                <div class="skyed-tile-actions">
                  <?php if ($pos !== ''): ?><span class="skyed-card__badge skyed-card__badge--tile"><?php echo self::esc($pos); ?></span><?php endif; ?>
                  <?php if ($a_zh): ?><button class="skyed-mini-play skyed-mini-play--hint" type="button" data-audio="<?php echo esc_attr($a_zh); ?>">中文提示</button><?php endif; ?>
                </div>
              </div>
            </article>
        <?php elseif ($theme === 'strict_dark'): ?>
            <article class="skyed-card skyed-card--compact">
              <div class="skyed-card__compact-media">
                <?php if ($img !== ''): ?>
                  <img src="<?php echo self::escu($img); ?>" alt="<?php echo esc_attr($en); ?>" loading="lazy">
                <?php else: ?>
                  <div class="skyed-card__missing"><span>No image</span></div>
                <?php endif; ?>
              </div>
              <div class="skyed-card__body skyed-card__body--compact">
                <div class="skyed-card__top">
                  <div>
                    <div class="skyed-card__en"><?php echo self::esc($en); ?></div>
                    <?php if ($zh !== ''): ?><div class="skyed-card__zh"><?php echo self::esc($zh); ?></div><?php endif; ?>
                  </div>
                  <?php if ($pos !== ''): ?><div class="skyed-card__badge"><?php echo self::esc($pos); ?></div><?php endif; ?>
                </div>
                <div class="skyed-audio-inline">
                  <?php if ($a_en): ?><button class="skyed-mini-play skyed-mini-play--dark" type="button" data-audio="<?php echo esc_attr($a_en); ?>">Play EN</button><?php endif; ?>
                  <?php if ($a_zh): ?><button class="skyed-mini-play skyed-mini-play--dark" type="button" data-audio="<?php echo esc_attr($a_zh); ?>">Play CN</button><?php endif; ?>
                </div>
              </div>
            </article>
        <?php else: ?>
            <article class="skyed-card <?php echo $theme === 'fun_mission' ? 'skyed-card--mission' : ''; ?>">
              <div class="skyed-card__media">
                <?php if ($img !== ''): ?>
                  <img src="<?php echo self::escu($img); ?>" alt="<?php echo esc_attr($en); ?>" loading="lazy">
                <?php else: ?>
                  <div class="skyed-card__missing"><span>No image</span></div>
                <?php endif; ?>
                <?php if ($theme === 'fun_mission'): ?><span class="skyed-step-badge">Step <?php echo intval($idx + 1); ?></span><?php endif; ?>
              </div>
              <div class="skyed-card__body">
                <div class="skyed-card__top">
                  <div>
                    <div class="skyed-card__en"><?php echo self::esc($en); ?></div>
                    <?php if ($zh !== ''): ?><div class="skyed-card__zh"><?php echo self::esc($zh); ?></div><?php endif; ?>
                  </div>
                  <?php if ($pos !== ''): ?><div class="skyed-card__badge"><?php echo self::esc($pos); ?></div><?php endif; ?>
                </div>
                <div class="skyed-audio-grid">
                  <?php if ($a_en): ?>
                    <div class="skyed-audio-box">
                      <div class="skyed-audio-box__label">English</div>
                      <audio controls preload="none" src="<?php echo self::escu($a_en); ?>"></audio>
                    </div>
                  <?php endif; ?>
                  <?php if ($a_zh): ?>
                    <div class="skyed-audio-box">
                      <div class="skyed-audio-box__label">Chinese</div>
                      <audio controls preload="none" src="<?php echo self::escu($a_zh); ?>"></audio>
                    </div>
                  <?php endif; ?>
                </div>
              </div>
            </article>
        <?php endif;
        return ob_get_clean();
    }

    private static function render_sentence_row(array $it, string $theme, int $idx) : string {
        $en   = isset($it['en']) ? (string)$it['en'] : '';
        $zh   = isset($it['zh']) ? (string)$it['zh'] : '';
        $a_en = isset($it['audio_en']) ? (string)$it['audio_en'] : '';
        $a_zh = isset($it['audio_zh']) ? (string)$it['audio_zh'] : '';
        ob_start(); ?>
        <article class="skyed-sent <?php echo $theme === 'strict_dark' ? 'skyed-sent--strict' : ($theme === 'fun_mission' ? 'skyed-sent--mission' : ($theme === 'sky_tiles' ? 'skyed-sent--tile' : '')); ?>">
          <?php if ($theme === 'fun_mission'): ?><div class="skyed-sent__marker"><?php echo intval($idx + 1); ?></div><?php endif; ?>
          <div class="skyed-sent__text">
            <?php if ($theme === 'sky_tiles'): ?>
              <div class="skyed-sent__tile-top">
                <div class="skyed-sent__marker skyed-sent__marker--tile"><?php echo intval($idx + 1); ?></div>
                <div>
                  <div class="skyed-sent__oral-title">Listen. Say it.</div>
                  <div class="skyed-sent__oral-note">Tap the blue button and repeat together.</div>
                </div>
              </div>
              <?php if ($en !== ''): ?><div class="skyed-sent__tile-bubble"><?php echo self::esc($en); ?></div><?php endif; ?>
            <?php else: ?>
              <?php if ($en !== ''): ?><div class="skyed-sent__line skyed-sent__line--en"><?php echo self::esc($en); ?></div><?php endif; ?>
              <?php if ($zh !== ''): ?><div class="skyed-sent__line skyed-sent__line--zh"><?php echo self::esc($zh); ?></div><?php endif; ?>
            <?php endif; ?>
          </div>
          <div class="skyed-sent__audio <?php echo $theme === 'sky_tiles' ? 'skyed-sent__audio--tile' : ''; ?>">
            <?php if ($theme === 'sky_tiles'): ?>
              <div class="skyed-sent__tile-actions">
                <?php if ($a_en): ?><button class="skyed-mini-play skyed-mini-play--tile-main" type="button" data-audio="<?php echo esc_attr($a_en); ?>">▶ Listen</button><?php endif; ?>
                <?php if ($a_zh): ?><button class="skyed-mini-play skyed-mini-play--hint" type="button" data-audio="<?php echo esc_attr($a_zh); ?>">中文</button><?php endif; ?>
              </div>
              <?php if ($zh !== ''): ?>
              <details class="skyed-sent__hint skyed-sent__hint--tile">
                <summary>Show meaning</summary>
                <div class="skyed-sent__hint-line skyed-sent__hint-line--zh"><?php echo self::esc($zh); ?></div>
              </details>
              <?php endif; ?>
            <?php else: ?>
              <?php if ($a_en): ?>
                <div class="skyed-audio-box">
                  <div class="skyed-audio-box__label">English</div>
                  <audio controls preload="none" src="<?php echo self::escu($a_en); ?>"></audio>
                </div>
              <?php endif; ?>
              <?php if ($a_zh): ?>
                <div class="skyed-audio-box">
                  <div class="skyed-audio-box__label">Chinese</div>
                  <audio controls preload="none" src="<?php echo self::escu($a_zh); ?>"></audio>
                </div>
              <?php endif; ?>
            <?php endif; ?>
          </div>
        </article>
        <?php return ob_get_clean();
    }

    private static function render_qa_section(array $qa, string $theme) : string {
        if ($theme !== 'sky' || empty($qa)) {
            return '';
        }
        ob_start(); ?>
        <section class="skyed-section skyed-section--qa">
          <div class="skyed-section__head">
            <div>
              <div class="skyed-section__eyebrow">Talk together</div>
              <h2 class="skyed-section__title">Questions and Answers</h2>
            </div>
            <div class="skyed-section__note">Use these short question-and-answer lines for guided speaking practice.</div>
          </div>
          <div class="skyed-grid skyed-grid--qa">
            <?php foreach ($qa as $i => $row): ?>
              <?php
                $q = is_array($row) && isset($row['q']) ? (string)$row['q'] : '';
                $a = is_array($row) && isset($row['a']) ? (string)$row['a'] : '';
                if ($q === '' && $a === '') {
                    continue;
                }
              ?>
              <article class="skyed-qa-card">
                <div class="skyed-qa-card__num"><?php echo intval($i + 1); ?></div>
                <div class="skyed-qa-card__body">
                  <?php if ($q !== ''): ?>
                    <div class="skyed-qa-card__label">Question</div>
                    <div class="skyed-qa-card__text skyed-qa-card__text--q"><?php echo self::esc($q); ?></div>
                  <?php endif; ?>
                  <?php if ($a !== ''): ?>
                    <div class="skyed-qa-card__label skyed-qa-card__label--answer">Answer</div>
                    <div class="skyed-qa-card__text skyed-qa-card__text--a"><?php echo self::esc($a); ?></div>
                  <?php endif; ?>
                </div>
              </article>
            <?php endforeach; ?>
          </div>
        </section>
        <?php return ob_get_clean();
    }

    private static function render_info_row(array $categories) : string {
        if (!self::SHOW_PUBLIC_CATEGORIES || empty($categories)) {
            return '';
        }
        ob_start(); ?>
        <section class="skyed-section skyed-section--meta">
          <div class="skyed-section__head">
            <div>
              <h2 class="skyed-section__title">Lesson info</h2>
            </div>
          </div>
          <div class="skyed-chip-row skyed-chip-row--meta">
            <?php foreach ($categories as $key => $value): ?>
              <?php if (is_scalar($value) && (string)$value !== ''): ?>
                <span class="skyed-chip skyed-chip--meta"><?php echo self::esc(str_replace('_', ' ', (string)$value)); ?></span>
              <?php endif; ?>
            <?php endforeach; ?>
          </div>
        </section>
        <?php return ob_get_clean();
    }

    private static function render_tag_games(array $tag_games, string $theme = 'sky') : string {
        if (!self::SHOW_PUBLIC_TAG_GAMES || empty($tag_games)) {
            return '';
        }
        $eyebrow = $theme === 'ng' ? 'Touch and listen' : 'Extra practice';
        $title = $theme === 'ng' ? 'NG tag_s' : 'More to try';
        $note = $theme === 'ng' ? 'Tap a lesson card and hear the word instantly.' : 'Optional extra activities linked to this lesson.';
        ob_start(); ?>
        <section class="skyed-section skyed-section--tag-games <?php echo $theme === 'ng' ? 'skyed-section--ng-tag' : ''; ?>">
          <div class="skyed-section__head">
            <div>
              <div class="skyed-section__eyebrow"><?php echo self::esc($eyebrow); ?></div>
              <h2 class="skyed-section__title"><?php echo self::esc($title); ?></h2>
            </div>
            <div class="skyed-section__note"><?php echo self::esc($note); ?></div>
          </div>
          <div class="skyed-tag-games <?php echo $theme === 'ng' ? 'skyed-tag-games--ng' : ''; ?>">
            <?php foreach ($tag_games as $game): ?>
              <?php if (!is_array($game)) { continue; } ?>
              <a class="skyed-tag-game <?php echo $theme === 'ng' ? 'skyed-tag-game--ng' : ''; ?>" href="<?php echo esc_url((string)($game['url'] ?? '')); ?>" target="_blank" rel="noopener">
                <div class="skyed-tag-game__title"><?php echo self::esc((string)($game['title'] ?? ($game['game_id'] ?? 'Tag game'))); ?></div>
                <div class="skyed-tag-game__meta"><?php echo self::esc((string)($game['tag'] ?? '')); ?></div>
                <?php if ($theme === 'ng'): ?><div class="skyed-tag-game__cta">Open touch-and-listen practice</div><?php endif; ?>
              </a>
            <?php endforeach; ?>
          </div>
        </section>
        <?php return ob_get_clean();
    }
    private static function render_extra_audio_section(array $items, string $theme = 'sky') : string {
        if (empty($items)) {
            return '';
        }
        $section_title = $theme === 'ng' ? 'Happy Practice' : 'Extra Audio';
        foreach ($items as $item) {
            if (!is_array($item)) { continue; }
            $candidate = isset($item['title']) ? trim((string)$item['title']) : '';
            if ($candidate !== '') {
                $section_title = $candidate;
                break;
            }
        }
        $is_happy = ($theme === 'ng');
        ob_start(); ?>
        <section class="skyed-section skyed-section--extra-audio <?php echo $is_happy ? 'skyed-section--happy-practice' : ''; ?>">
          <div class="skyed-section__head">
            <div>
              <div class="skyed-section__eyebrow"><?php echo $is_happy ? 'Happy practice' : 'Special lesson'; ?></div>
              <h2 class="skyed-section__title"><?php echo self::esc($section_title); ?></h2>
            </div>
            <div class="skyed-section__note"><?php echo $is_happy ? 'Uploaded sing-along and repeat-after-me tracks for this NG lesson.' : 'Teacher-routed local audio added for this lesson.'; ?></div>
          </div>
          <div class="<?php echo $is_happy ? 'skyed-happy-audio' : 'skyed-extra-audio'; ?>">
            <?php foreach ($items as $item): if (!is_array($item)) { continue; } $url = isset($item['url']) ? (string)$item['url'] : ''; if ($url === '') { continue; } $label = isset($item['label']) ? (string)$item['label'] : $section_title; ?>
              <?php if ($is_happy): ?>
                <article class="skyed-happy-audio__track">
                  <div class="skyed-happy-audio__sparkle">♪</div>
                  <div class="skyed-happy-audio__body">
                    <div class="skyed-happy-audio__label"><?php echo self::esc($label); ?></div>
                    <div class="skyed-happy-audio__sub">Tap play and practise together.</div>
                    <audio controls preload="none" src="<?php echo self::escu($url); ?>"></audio>
                  </div>
                </article>
              <?php else: ?>
                <article class="skyed-extra-audio__track">
                  <div class="skyed-extra-audio__label"><?php echo self::esc($label); ?></div>
                  <audio controls preload="none" src="<?php echo self::escu($url); ?>"></audio>
                </article>
              <?php endif; ?>
            <?php endforeach; ?>
          </div>
        </section>
        <?php return ob_get_clean();
    }

    private static function render_picture_reader(array $payload, string $theme, string $title, array $tags, array $categories, array $tag_games) : string {
        $sentences = isset($payload['sentences']) && is_array($payload['sentences']) ? $payload['sentences'] : [];
        $reader = isset($payload['picture_reader']) && is_array($payload['picture_reader']) ? $payload['picture_reader'] : [];
        $cover = isset($reader['cover_image']) ? (string)$reader['cover_image'] : '';
        ob_start(); ?>
        <div class="skyed-lesson skyed-lesson--reader" data-theme="<?php echo esc_attr($theme); ?>">
          <div class="skyed-shell skyed-shell--reader">
            <section class="skyed-hero skyed-hero--<?php echo esc_attr($theme); ?>">
              <div class="skyed-hero__main">
                <div class="skyed-kicker">Sky Reading Frame</div>
                <h2 class="skyed-title"><?php echo self::esc($title); ?></h2>
                <p class="skyed-subtitle">Touch any line to hear the bilingual reading. English plays first, then Chinese.</p>
                <?php if (!empty($tags)): ?><div class="skyed-tag-row"><?php foreach ($tags as $tag): ?><span class="skyed-tag"><?php echo self::esc((string)$tag); ?></span><?php endforeach; ?></div><?php endif; ?>
              </div>
              <div class="skyed-hero__meta">
                <?php echo self::stat_chip(count($sentences) . ' lines'); ?>
                <?php echo self::stat_chip('Touch to listen'); ?>
              </div>
            </section>
            <section class="skyed-section skyed-section--reader">
              <div class="skyed-reader-sheet">
                <div class="skyed-reader-sheet__top">
                  <div class="skyed-reader-pill">Interactive picture text</div>
                  <div class="skyed-reader-note">Single reading block for fast mobile replay.</div>
                </div>
                <div class="skyed-reader-flow">
                  <?php foreach ($sentences as $i => $it):
                    $en = isset($it['en']) ? trim((string)$it['en']) : '';
                    $zh = isset($it['zh']) ? trim((string)$it['zh']) : '';
                    $a_en = isset($it['audio_en']) ? (string)$it['audio_en'] : '';
                    $a_zh = isset($it['audio_zh']) ? (string)$it['audio_zh'] : '';
                    if ($en === '' && $zh === '') { continue; }
                  ?>
                    <button class="skyed-reader-line" type="button" data-audio-en="<?php echo esc_attr($a_en); ?>" data-audio-zh="<?php echo esc_attr($a_zh); ?>" aria-label="Play line <?php echo intval($i + 1); ?>">
                      <span class="skyed-reader-line__idx"><?php echo intval($i + 1); ?></span>
                      <span class="skyed-reader-line__body">
                        <?php if ($en !== ''): ?>
                          <span class="skyed-reader-line__label">English</span>
                          <span class="skyed-reader-line__en"><?php echo self::esc($en); ?></span>
                        <?php endif; ?>
                        <?php if ($zh !== ''): ?>
                          <span class="skyed-reader-line__label skyed-reader-line__label--zh">Chinese</span>
                          <span class="skyed-reader-line__zh"><?php echo self::esc($zh); ?></span>
                        <?php endif; ?>
                      </span>
                      <span class="skyed-reader-line__hint">Tap to listen</span>
                    </button>
                  <?php endforeach; ?>
                </div>
                <?php if ($cover !== ''): ?>
                  <div class="skyed-reader-sheet__art"><img src="<?php echo self::escu($cover); ?>" alt="<?php echo esc_attr($title); ?>" loading="lazy"></div>
                <?php endif; ?>
              </div>
            </section>
            <?php echo self::render_tag_games($tag_games, $theme); ?>
          </div>
        </div>
        <script>
        (function(){
          const root = document.currentScript ? document.currentScript.previousElementSibling : document.querySelector('.skyed-lesson--reader');
          const entries = root ? root.querySelectorAll('.skyed-reader-line') : document.querySelectorAll('.skyed-reader-line');
          let current = null;
          let currentEntry = null;
          function clearState(){
            entries.forEach(function(el){ el.classList.remove('is-playing','is-speaking-en','is-speaking-zh'); });
          }
          function stopCurrent(){
            if (current) {
              try { current.pause(); current.currentTime = 0; } catch(e) {}
            }
            current = null;
            currentEntry = null;
            clearState();
          }
          function playUrl(url, onEnd){
            if (!url) { if (onEnd) onEnd(); return; }
            const audio = new Audio(url);
            current = audio;
            audio.onended = function(){ if (onEnd) onEnd(); };
            audio.onerror = function(){ if (onEnd) onEnd(); };
            audio.play().catch(function(){ if (onEnd) onEnd(); });
          }
          function playEntry(entry){
            if (!entry) return;
            if (currentEntry === entry) { stopCurrent(); return; }
            stopCurrent();
            currentEntry = entry;
            const enUrl = entry.getAttribute('data-audio-en') || '';
            const zhUrl = entry.getAttribute('data-audio-zh') || '';
            entry.classList.add('is-playing');
            if (enUrl) {
              entry.classList.add('is-speaking-en');
              playUrl(enUrl, function(){
                entry.classList.remove('is-speaking-en');
                if (zhUrl) {
                  entry.classList.add('is-speaking-zh');
                  playUrl(zhUrl, function(){ stopCurrent(); });
                } else {
                  stopCurrent();
                }
              });
            } else if (zhUrl) {
              entry.classList.add('is-speaking-zh');
              playUrl(zhUrl, function(){ stopCurrent(); });
            }
          }
          entries.forEach(function(entry){
            entry.addEventListener('click', function(){ playEntry(entry); });
            entry.addEventListener('keydown', function(ev){
              if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); playEntry(entry); }
            });
          });
        })();
        </script>
        <?php return ob_get_clean();
    }

    public static function shortcode($atts, $content = null) : string {
        $atts = shortcode_atts(['data_url' => '', 'theme' => ''], $atts, self::SHORTCODE);
        $payload = self::fetch_payload((string)$atts['data_url']);
        if (empty($payload)) {
            return '<div class="skyed-lesson"><div class="skyed-shell"><div class="skyed-alert">SkyEd lesson payload not found. Check data_url.</div></div></div>';
        }

        $title      = isset($payload['title']) && is_string($payload['title']) ? $payload['title'] : 'Lesson';
        $tags       = isset($payload['tags']) && is_array($payload['tags']) ? $payload['tags'] : [];
        $vocab      = isset($payload['vocab']) && is_array($payload['vocab']) ? $payload['vocab'] : [];
        $sentences  = isset($payload['sentences']) && is_array($payload['sentences']) ? $payload['sentences'] : [];
        $categories = isset($payload['categories']) && is_array($payload['categories']) ? $payload['categories'] : [];
        $tag_games  = isset($payload['tag_games']) && is_array($payload['tag_games']) ? $payload['tag_games'] : [];
        $page_kind  = isset($payload['page_kind']) && is_string($payload['page_kind']) ? $payload['page_kind'] : 'lesson';
        $extra_audio = isset($payload['extra_audio']) && is_array($payload['extra_audio']) ? $payload['extra_audio'] : [];
        $qa          = isset($payload['qa']) && is_array($payload['qa']) ? $payload['qa'] : [];

        $practice = [];
        if (isset($payload['practice']) && is_array($payload['practice'])) {
            $practice = $payload['practice'];
        } elseif (isset($payload['quiz']) && is_array($payload['quiz'])) {
            $practice = $payload['quiz'];
        }

        $meta               = isset($payload['meta']) && is_array($payload['meta']) ? $payload['meta'] : [];
        $consistency        = isset($payload['consistency']) && is_array($payload['consistency']) ? $payload['consistency'] : [];
        $theme              = self::theme_name((string)($atts['theme'] ?: ($payload['renderer_theme'] ?? ($meta['theme_variant'] ?? 'sky'))));
        $copy               = self::theme_copy($theme);
        $practice_title     = isset($practice['section_title']) && is_string($practice['section_title']) ? $practice['section_title'] : 'Practice';
        $practice_questions = isset($practice['questions']) && is_array($practice['questions']) ? $practice['questions'] : [];
        $practice_family    = isset($practice['practice_family']) && is_string($practice['practice_family']) ? $practice['practice_family'] : 'lesson_practice';
        $practice_subtitle  = isset($practice['subtitle']) && is_string($practice['subtitle']) ? $practice['subtitle'] : (count($practice_questions) . ' questions');
        $renderer_mode      = isset($practice['renderer_mode']) && is_string($practice['renderer_mode']) ? $practice['renderer_mode'] : ($theme === 'sky_tiles' ? 'kid_single' : ($theme === 'fun_mission' || $theme === 'strict_dark' ? 'single' : 'list'));

        if ($page_kind === 'picture_reader') {
            return self::render_picture_reader($payload, $theme, $title, $tags, $categories, $tag_games);
        }

        $uid          = 'skyed_' . wp_rand(100000, 999999);
        $count_vocab  = count($vocab);
        $count_sent   = count($sentences);
        $count_q      = count($practice_questions);
        $unused_vocab = isset($consistency['vocab_not_seen_in_sentences']) && is_array($consistency['vocab_not_seen_in_sentences']) ? $consistency['vocab_not_seen_in_sentences'] : [];
        $warnings = [];
        if (!empty($unused_vocab)) {
            $warnings[] = 'Not yet used in sentences: ' . implode(', ', array_map('esc_html', $unused_vocab));
        }

        ob_start(); ?>
        <div class="skyed-lesson" data-theme="<?php echo esc_attr($theme); ?>">
          <div class="skyed-shell">

            <section class="skyed-hero skyed-hero--<?php echo esc_attr($theme); ?>">
              <div class="skyed-hero__main">
                <div class="skyed-kicker"><?php echo self::esc($copy['kicker']); ?></div>
                <h2 class="skyed-title"><?php echo self::esc($title); ?></h2>
                <p class="skyed-subtitle"><?php echo self::esc($copy['subtitle']); ?></p>
                <?php if (!empty($tags)): ?>
                  <div class="skyed-tag-row">
                    <?php foreach ($tags as $tag): ?>
                      <span class="skyed-tag"><?php echo self::esc((string)$tag); ?></span>
                    <?php endforeach; ?>
                  </div>
                <?php endif; ?>
              </div>
              <div class="skyed-hero__meta">
                <?php echo self::stat_chip($count_vocab . ' words'); ?>
                <?php echo self::stat_chip($count_sent . ' sentences'); ?>
                <?php echo self::stat_chip($count_q . ' practice'); ?>
                <?php echo self::stat_chip('5–10 min'); ?>
              </div>
            </section>

            <?php if (!empty($warnings)): ?>
              <div class="skyed-alert skyed-alert--soft">
                <?php foreach ($warnings as $warning): ?><div><?php echo wp_kses_post($warning); ?></div><?php endforeach; ?>
              </div>
            <?php endif; ?>

            <?php // Categories are preserved in payload but hidden on public pages for now. ?>
            <?php echo self::render_tag_games($tag_games, $theme); ?>

            <section class="skyed-section skyed-section--vocab">
              <div class="skyed-section__head">
                <div>
                  <div class="skyed-section__eyebrow"><?php echo $theme === 'sky_tiles' ? 'Tap and hear' : ($theme === 'fun_mission' ? 'Warm-up words' : 'Core words'); ?></div>
                  <h2 class="skyed-section__title"><?php echo $theme === 'sky_tiles' ? 'Picture Tiles' : 'Vocabulary'; ?></h2>
                </div>
                <div class="skyed-section__note"><?php echo self::esc($copy['vocab_note']); ?></div>
              </div>
              <div class="skyed-grid skyed-grid--cards <?php echo $theme === 'strict_dark' ? 'skyed-grid--compact' : ''; ?>">
                <?php foreach ($vocab as $i => $it) { echo self::render_vocab_card($it, $theme, $i); } ?>
              </div>
            </section>

            <section class="skyed-section skyed-section--sentences">
              <div class="skyed-section__head">
                <div>
                  <div class="skyed-section__eyebrow"><?php echo $theme === 'sky_tiles' ? 'Say it' : ($theme === 'strict_dark' ? 'Usage lines' : 'Use it'); ?></div>
                  <h2 class="skyed-section__title"><?php echo $theme === 'sky_tiles' ? 'Oral Practice' : 'Sentence Practice'; ?></h2>
                </div>
                <div class="skyed-section__note"><?php echo self::esc($copy['sent_note']); ?></div>
              </div>
              <div class="skyed-grid skyed-grid--sentences">
                <?php foreach ($sentences as $i => $it) { echo self::render_sentence_row($it, $theme, $i); } ?>
              </div>
            </section>

            <?php echo self::render_qa_section($qa, $theme); ?>

            <?php echo self::render_extra_audio_section($extra_audio, $theme); ?>

            <section class="skyed-section skyed-section--practice">
              <div class="skyed-section__head">
                <div>
                  <div class="skyed-section__eyebrow"><?php echo $theme === 'sky_tiles' ? 'Listen and tap' : self::esc($practice_family); ?></div>
                  <h2 class="skyed-section__title"><?php echo $theme === 'sky_tiles' ? 'Picture Quiz' : self::esc($practice_title); ?></h2>
                </div>
                <div class="skyed-section__note"><?php echo self::esc($copy['practice_note']); ?></div>
              </div>

              <?php if (!empty($practice_questions)): ?>
                <div class="skyed-practice" data-renderer-mode="<?php echo esc_attr($renderer_mode); ?>">
                  <div class="skyed-practice__toolbar">
                    <div class="skyed-practice__meta">
                      <div class="skyed-practice__title"><?php echo self::esc($practice_title); ?></div>
                      <div class="skyed-practice__sub"><?php echo self::esc($practice_subtitle); ?></div>
                    </div>
                    <div class="skyed-practice__nav" id="<?php echo esc_attr($uid); ?>_nav"></div>
                  </div>

                  <div class="skyed-progress"><div class="skyed-progress__bar" id="<?php echo esc_attr($uid); ?>_bar"></div></div>
                  <div id="<?php echo esc_attr($uid); ?>_app"></div>
                  <div class="skyed-practice__footer">
                    <div class="skyed-practice__actions">
                      <button class="skyed-btn skyed-btn--ghost" type="button" id="<?php echo esc_attr($uid); ?>_reset"><?php echo $theme === 'sky_tiles' ? 'Start again' : 'Retry'; ?></button>
                      <button class="skyed-btn skyed-btn--primary" type="button" id="<?php echo esc_attr($uid); ?>_submit"><?php echo $theme === 'sky_tiles' ? 'Show stars' : 'Check answers'; ?></button>
                    </div>
                    <div class="skyed-result" id="<?php echo esc_attr($uid); ?>_result"></div>
                  </div>
                  <script type="application/json" id="<?php echo esc_attr($uid); ?>_data"><?php echo wp_json_encode($practice); ?></script>
                </div>

                <script>
                (function(){
                  const uid = <?php echo json_encode($uid); ?>;
                  const theme = <?php echo json_encode($theme); ?>;
                  const dataEl = document.getElementById(uid + "_data");
                  const app = document.getElementById(uid + "_app");
                  const resultEl = document.getElementById(uid + "_result");
                  const btn = document.getElementById(uid + "_submit");
                  const resetBtn = document.getElementById(uid + "_reset");
                  const bar = document.getElementById(uid + "_bar");
                  const nav = document.getElementById(uid + "_nav");
                  if (!dataEl || !app || !btn || !resetBtn) return;

                  let practice = {};
                  try { practice = JSON.parse(dataEl.textContent || "{}"); } catch(e) { practice = {}; }
                  const questions = practice.questions || [];
                  const answers = {};
                  let currentIndex = 0;
                  let autoScrollPending = false;
                  const rendererMode = practice.renderer_mode || (theme === 'sky_tiles' ? 'kid_single' : ((theme === 'strict_dark') ? 'single' : ((theme === 'fun_mission') ? 'mission_auto' : 'list')));

                  function normalizeChoice(c){
                    if (typeof c === 'string') return { text: c, img: '', subtext: '', audio: '' };
                    if (c && typeof c === 'object') return { text: c.text || '', img: c.img || '', subtext: c.subtext || '', audio: c.audio || '' };
                    return { text: '—', img: '', subtext: '', audio: '' };
                  }

                  function playAudio(url){
                    if (!url) return;
                    try {
                      const a = new Audio(url);
                      a.play().catch(()=>{});
                    } catch(e) {}
                  }

                  function makePlayButton(url, label, cls){
                    if (!url) return null;
                    const b = document.createElement('button');
                    b.type = 'button';
                    b.className = cls || 'skyed-mini-play';
                    b.textContent = label || 'Play';
                    b.onclick = () => playAudio(url);
                    return b;
                  }

                  function updateProgress(){
                    const answered = Object.keys(answers).length;
                    const pct = questions.length ? Math.round((answered / questions.length) * 100) : 0;
                    if (bar) bar.style.width = pct + '%';
                  }

                  function buildQuestionCard(q, idx, singleMode){
                    const card = document.createElement('article');
                    card.className = 'skyed-qcard' + (rendererMode === 'kid_single' ? ' skyed-qcard--kid' : '') + (rendererMode === 'single' ? ' skyed-qcard--single' : '');
                    card.dataset.index = String(idx);

                    const head = document.createElement('div');
                    head.className = 'skyed-qcard__head';
                    const num = document.createElement('div');
                    num.className = 'skyed-qcard__num';
                    num.textContent = String(idx + 1);
                    head.appendChild(num);

                    const body = document.createElement('div');
                    body.className = 'skyed-qcard__body';
                    const label = document.createElement('div');
                    label.className = 'skyed-q__label';
                    label.textContent = q.action_label || (q.kind || 'Question').replace(/_/g, ' ');
                    body.appendChild(label);

                    const qt = document.createElement('div');
                    qt.className = 'skyed-q';
                    qt.textContent = q.q || '';
                    body.appendChild(qt);

                    if (q.helper) {
                      const helper = document.createElement('div');
                      helper.className = 'skyed-q__helper';
                      helper.textContent = q.helper;
                      body.appendChild(helper);
                    }

                    if (q.prompt_audio) {
                      const promptAudio = document.createElement('div');
                      promptAudio.className = 'skyed-q__audio' + ((rendererMode === 'kid_single' || rendererMode === 'mission_auto') ? ' skyed-q__audio--sticky' : '');
                      const btnPlay = makePlayButton(q.prompt_audio, rendererMode === 'kid_single' ? 'Listen' : 'Listen', 'skyed-prompt-play');
                      if (btnPlay) promptAudio.appendChild(btnPlay);
                      body.appendChild(promptAudio);
                    }

                    if (q.prompt_image) {
                      const promptMedia = document.createElement('div');
                      promptMedia.className = 'skyed-q__prompt';
                      const im = document.createElement('img');
                      im.src = q.prompt_image;
                      im.alt = q.q || ('question ' + (idx + 1));
                      promptMedia.appendChild(im);
                      body.appendChild(promptMedia);
                    }

                    const choicesWrap = document.createElement('div');
                    choicesWrap.className = 'skyed-choices';
                    (q.choices || []).forEach((rawChoice, ci) => {
                      const ch = normalizeChoice(rawChoice);
                      const choiceBtn = document.createElement('button');
                      choiceBtn.type = 'button';
                      choiceBtn.className = 'skyed-choice';
                      const inner = document.createElement('div');
                      inner.className = 'skyed-choice__inner';

                      if (ch.img) {
                        const im = document.createElement('img');
                        im.src = ch.img;
                        im.alt = ch.text || ('choice ' + (ci + 1));
                        inner.appendChild(im);
                      }

                      const textWrap = document.createElement('div');
                      textWrap.className = 'skyed-choice__text';
                      if (rendererMode !== 'kid_single') {
                        if (ch.text) {
                          const main = document.createElement('div');
                          main.className = 'skyed-choice__main';
                          main.textContent = ch.text;
                          textWrap.appendChild(main);
                        }
                        if (ch.subtext) {
                          const sub = document.createElement('div');
                          sub.className = 'skyed-choice__sub';
                          sub.textContent = ch.subtext;
                          textWrap.appendChild(sub);
                        }
                        if (textWrap.children.length) inner.appendChild(textWrap);
                        if (ch.audio) {
                          const audioBtn = makePlayButton(ch.audio, '🔊', 'skyed-choice__play');
                          if (audioBtn) inner.appendChild(audioBtn);
                        }
                      }

                      choiceBtn.appendChild(inner);
                      choiceBtn.onclick = () => {
                        answers[idx] = ci;
                        const expected = Number(q.answer_index);
                        [...choicesWrap.querySelectorAll('.skyed-choice')].forEach(x => x.classList.remove('active','wrong','correct'));
                        choiceBtn.classList.add('active');
                        updateProgress();
                        if (rendererMode === 'kid_single') {
                          if (ci === expected) {
                            choiceBtn.classList.add('correct');
                            if (currentIndex < questions.length - 1) {
                              window.setTimeout(() => { currentIndex += 1; autoScrollPending = true; render(); }, 420);
                            } else {
                              window.setTimeout(() => btn.click(), 420);
                            }
                          } else {
                            choiceBtn.classList.add('wrong');
                            window.setTimeout(() => { choiceBtn.classList.remove('wrong','active'); }, 520);
                            window.setTimeout(() => { if (q.prompt_audio) playAudio(q.prompt_audio); }, 180);
                          }
                        } else if (rendererMode === 'mission_auto') {
                          if (ci === expected) {
                            choiceBtn.classList.add('correct');
                          } else {
                            choiceBtn.classList.add('wrong');
                            const correctBtn = choicesWrap.querySelectorAll('.skyed-choice')[expected];
                            if (correctBtn) correctBtn.classList.add('correct');
                          }
                          if (currentIndex < questions.length - 1) {
                            window.setTimeout(() => { currentIndex += 1; autoScrollPending = true; render(); }, 520);
                          } else {
                            window.setTimeout(() => btn.click(), 520);
                          }
                        }
                      };
                      choicesWrap.appendChild(choiceBtn);
                    });

                    body.appendChild(choicesWrap);
                    head.appendChild(body);
                    card.appendChild(head);
                    return card;
                  }

                  function renderNav(){
                    if (!nav) return;
                    nav.innerHTML = '';
                    if (!(rendererMode === 'single' || rendererMode === 'kid_single' || rendererMode === 'mission_auto')) return;
                    const meta = document.createElement('div');
                    meta.className = 'skyed-nav-meta';
                    meta.textContent = (rendererMode === 'kid_single' ? 'Round ' : (rendererMode === 'mission_auto' ? 'Checkpoint ' : 'Question ')) + (currentIndex + 1) + ' of ' + questions.length;
                    nav.appendChild(meta);
                    if (rendererMode === 'kid_single' || rendererMode === 'mission_auto') return;
                    const prev = document.createElement('button');
                    prev.type = 'button';
                    prev.className = 'skyed-btn skyed-btn--ghost';
                    prev.textContent = 'Previous';
                    prev.disabled = currentIndex <= 0;
                    prev.onclick = () => { if (currentIndex > 0) { currentIndex -= 1; render(); } };
                    const next = document.createElement('button');
                    next.type = 'button';
                    next.className = 'skyed-btn skyed-btn--ghost';
                    next.textContent = currentIndex >= questions.length - 1 ? 'Last question' : 'Next';
                    next.disabled = currentIndex >= questions.length - 1;
                    next.onclick = () => { if (currentIndex < questions.length - 1) { currentIndex += 1; render(); } };
                    nav.appendChild(prev);
                    nav.appendChild(next);
                  }

                  function render(){
                    app.innerHTML = '';
                    renderNav();
                    if (rendererMode === 'single' || rendererMode === 'kid_single' || rendererMode === 'mission_auto') {
                      const q = questions[currentIndex];
                      if (q) app.appendChild(buildQuestionCard(q, currentIndex, true));
                    } else {
                      questions.forEach((q, idx) => app.appendChild(buildQuestionCard(q, idx, false)));
                    }
                    [...app.querySelectorAll('.skyed-qcard')].forEach(card => {
                      const idx = Number(card.dataset.index || '-1');
                      const chosen = answers[idx];
                      if (typeof chosen === 'number') {
                        const btns = [...card.querySelectorAll('.skyed-choice')];
                        if (btns[chosen]) btns[chosen].classList.add('active');
                      }
                    });
                    if ((rendererMode === 'kid_single' || rendererMode === 'mission_auto') && autoScrollPending) {
                      window.requestAnimationFrame(() => {
                        const activeCard = app.querySelector('.skyed-qcard');
                        if (activeCard && activeCard.scrollIntoView) {
                          activeCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        }
                        autoScrollPending = false;
                      });
                    }
                  }

                  function resetPractice(){
                    Object.keys(answers).forEach(k => delete answers[k]);
                    currentIndex = 0;
                    autoScrollPending = false;
                    if (resultEl) resultEl.innerHTML = '';
                    updateProgress();
                    render();
                  }

                  resetBtn.onclick = resetPractice;
                  if (rendererMode === 'mission_auto') {
                    btn.style.display = 'none';
                    resetBtn.textContent = 'Start mission again';
                  }
                  btn.onclick = () => {
                    let score = 0;
                    questions.forEach((q, idx) => {
                      if (Number(answers[idx]) === Number(q.answer_index)) score += 1;
                    });
                    if (rendererMode === 'kid_single') {
                      const stars = '⭐'.repeat(score);
                      resultEl.innerHTML = '<div class="skyed-alert skyed-alert--stars">Stars: <b>' + score + '</b> / ' + questions.length + '<div class="skyed-stars">' + stars + '</div></div>';
                    } else if (rendererMode === 'mission_auto') {
                      resultEl.innerHTML = '<div class="skyed-alert"><b>Mission complete</b><br>Score: <b>' + score + '</b> / ' + questions.length + '</div>';
                    } else {
                      resultEl.innerHTML = '<div class="skyed-alert">Score: <b>' + score + '</b> / ' + questions.length + '</div>';
                    }
                    render();
                    [...app.querySelectorAll('.skyed-qcard')].forEach(card => {
                      const idx = Number(card.dataset.index || '-1');
                      const q = questions[idx] || {};
                      const expected = Number(q.answer_index);
                      const chosen = (idx in answers) ? Number(answers[idx]) : -1;
                      const buttons = [...card.querySelectorAll('.skyed-choice')];
                      buttons.forEach((b, bi) => {
                        b.classList.remove('correct', 'wrong');
                        if (bi === expected) b.classList.add('correct');
                        if (bi === chosen && chosen !== expected) b.classList.add('wrong');
                      });
                    });
                  };

                  render();
                  updateProgress();
                  document.querySelectorAll('.skyed-tile-media[data-play="en"], .skyed-mini-play[data-audio]').forEach(btnEl => {
                    if (btnEl.dataset.bound === '1') return;
                    btnEl.dataset.bound = '1';
                    btnEl.addEventListener('click', function(ev){
                      const tile = this.closest('.skyed-card--tile'); const url = this.getAttribute('data-audio') || (tile ? (tile.getAttribute('data-audio-en') || '') : '');
                      if (url) {
                        ev.preventDefault();
                        playAudio(url);
                        const tile = this.closest('.skyed-card--tile');
                        if (tile) {
                          tile.classList.remove('is-playing');
                          void tile.offsetWidth;
                          tile.classList.add('is-playing');
                          setTimeout(() => tile.classList.remove('is-playing'), 320);
                        }
                      }
                    });
                  });
                })();
                </script>
              <?php else: ?>
                <div class="skyed-alert">Practice data missing in payload. Re-run generation.</div>
              <?php endif; ?>
            </section>
          </div>
        </div>
        <?php
        return ob_get_clean();
    }
}

SkyEd_Lesson_Renderer::init();
