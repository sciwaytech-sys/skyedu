<?php
/**
 * Plugin Name: SkyEd Lesson Renderer
 * Description: Renders SkyEd-generated lesson payload data (cards + audio + practice) via shortcode.
 * Version: 0.1.2
 * Author: Sky Education
 */

if (!defined('ABSPATH')) { exit; }

class SkyEd_Lesson_Renderer {
    const SHORTCODE = 'skyed_lesson';
    const CSS_HANDLE = 'skyed-lesson-renderer';
    const BOOTSTRAP_HANDLE = 'skyed-bootstrap5';

    public static function init() : void {
        add_action('wp_enqueue_scripts', [__CLASS__, 'enqueue_assets']);
        add_shortcode(self::SHORTCODE, [__CLASS__, 'shortcode']);

        // Allow payload uploads from the automation pipeline.
        add_filter('upload_mimes', [__CLASS__, 'allow_payload_mimes'], 10, 2);
        add_filter('wp_check_filetype_and_ext', [__CLASS__, 'fix_payload_filetype'], 10, 5);
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
            return [
                'ext' => 'json',
                'type' => 'application/json',
                'proper_filename' => $filename,
            ];
        }
        if ($ext === 'txt') {
            return [
                'ext' => 'txt',
                'type' => 'text/plain',
                'proper_filename' => $filename,
            ];
        }
        return $data;
    }

    public static function enqueue_assets() : void {
        wp_register_style(
            self::BOOTSTRAP_HANDLE,
            'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
            [],
            '5.3.3'
        );
        wp_enqueue_style(self::BOOTSTRAP_HANDLE);

        wp_register_style(
            self::CSS_HANDLE,
            plugins_url('assets/skyed-lesson.css', __FILE__),
            [self::BOOTSTRAP_HANDLE],
            '0.1.2'
        );
        wp_enqueue_style(self::CSS_HANDLE);
    }

    private static function fetch_payload(string $url) : array {
        $url = trim($url);
        if ($url === '') { return []; }

        $cache_key = 'skyed_payload_' . md5($url);
        $cached = get_transient($cache_key);
        if (is_array($cached) && !empty($cached)) {
            return $cached;
        }

        $resp = wp_remote_get($url, [
            'timeout' => 15,
            'redirection' => 3,
            'headers' => ['Accept' => 'application/json, text/plain, */*']
        ]);

        if (is_wp_error($resp)) { return []; }

        $code = wp_remote_retrieve_response_code($resp);
        $body = wp_remote_retrieve_body($resp);
        if ($code < 200 || $code >= 300 || !is_string($body) || $body === '') {
            return [];
        }

        $data = json_decode($body, true);
        if (!is_array($data)) { return []; }

        set_transient($cache_key, $data, 10 * MINUTE_IN_SECONDS);
        return $data;
    }

    private static function esc($s) : string {
        return esc_html((string)$s);
    }

    private static function escu($s) : string {
        return esc_url((string)$s);
    }

    private static function theme_name(string $theme) : string {
        $theme = strtolower(trim($theme));
        if (in_array($theme, ['strict', 'fun', 'sky'], true)) {
            return $theme;
        }
        return 'sky';
    }

    public static function shortcode($atts, $content = null) : string {
        $atts = shortcode_atts([
            'data_url' => '',
            'theme' => '',
        ], $atts, self::SHORTCODE);

        $payload = self::fetch_payload((string)$atts['data_url']);
        if (empty($payload)) {
            return '<div class="skyed-lesson container my-4"><div class="alert alert-warning">SkyEd lesson payload not found. Check data_url.</div></div>';
        }

        $title = isset($payload['title']) ? (string)$payload['title'] : 'Lesson';
        $vocab = isset($payload['vocab']) && is_array($payload['vocab']) ? $payload['vocab'] : [];
        $sentences = isset($payload['sentences']) && is_array($payload['sentences']) ? $payload['sentences'] : [];
        $practice = [];
        if (isset($payload['practice']) && is_array($payload['practice'])) {
            $practice = $payload['practice'];
        } elseif (isset($payload['quiz']) && is_array($payload['quiz'])) {
            $practice = $payload['quiz'];
        }

        $meta = isset($payload['meta']) && is_array($payload['meta']) ? $payload['meta'] : [];
        $theme = self::theme_name((string)($atts['theme'] ?: ($meta['theme_variant'] ?? 'sky')));
        $practice_title = isset($practice['section_title']) ? (string)$practice['section_title'] : 'Practice';
        $practice_questions = isset($practice['questions']) && is_array($practice['questions']) ? $practice['questions'] : [];
        $practice_family = isset($practice['practice_family']) ? (string)$practice['practice_family'] : 'lesson_practice';

        $uid = 'skyed_' . wp_rand(100000, 999999);
        $count_vocab = count($vocab);
        $count_sent = count($sentences);
        $count_q = count($practice_questions);

        ob_start();
        ?>
        <div class="skyed-lesson" data-theme="<?php echo esc_attr($theme); ?>">
          <div class="container py-4 py-lg-5">
            <section class="skyed-shell">
              <div class="skyed-hero shadow-sm">
                <div class="skyed-hero__main">
                  <div class="skyed-kicker">Sky Education</div>
                  <h1 class="skyed-title"><?php echo self::esc($title); ?></h1>
                  <p class="skyed-subtitle mb-0">Vocabulary → Sentences → Practice</p>
                </div>
                <div class="skyed-hero__meta">
                  <span class="skyed-chip"><?php echo esc_html($count_vocab); ?> cards</span>
                  <span class="skyed-chip"><?php echo esc_html($count_sent); ?> sentences</span>
                  <span class="skyed-chip"><?php echo esc_html($count_q); ?> practice items</span>
                  <span class="skyed-chip">5–10 min</span>
                </div>
              </div>

              <section class="skyed-section mt-4">
                <div class="skyed-section__head">
                  <div>
                    <div class="skyed-section__eyebrow">Core words</div>
                    <h2 class="skyed-section__title">Vocabulary Cards</h2>
                  </div>
                  <div class="skyed-section__note">Listen → repeat twice → say one sentence.</div>
                </div>

                <div class="row g-3">
                  <?php foreach ($vocab as $it):
                    $en = isset($it['en']) ? (string)$it['en'] : '';
                    $zh = isset($it['zh']) ? (string)$it['zh'] : '';
                    $img = isset($it['img']) ? (string)$it['img'] : '';
                    $a_en = isset($it['audio_en']) ? (string)$it['audio_en'] : '';
                    $a_zh = isset($it['audio_zh']) ? (string)$it['audio_zh'] : '';
                  ?>
                  <div class="col-12 col-sm-6 col-xl-4">
                    <article class="card skyed-card h-100 border-0 shadow-sm">
                      <div class="skyed-card__media ratio ratio-4x3">
                        <?php if ($img !== ''): ?>
                          <img src="<?php echo self::escu($img); ?>" alt="<?php echo esc_attr($en); ?>" loading="lazy">
                        <?php else: ?>
                          <div class="skyed-card__missing"><span>No image</span></div>
                        <?php endif; ?>
                      </div>
                      <div class="card-body skyed-card__body">
                        <div class="d-flex align-items-start justify-content-between gap-3 flex-wrap">
                          <div>
                            <div class="skyed-card__en"><?php echo self::esc($en); ?></div>
                            <?php if ($zh !== ''): ?><div class="skyed-card__zh"><?php echo self::esc($zh); ?></div><?php endif; ?>
                          </div>
                          <div class="skyed-card__badge">Word</div>
                        </div>

                        <div class="skyed-audio-grid mt-3">
                          <?php if ($a_en !== ''): ?>
                            <div class="skyed-audio-box">
                              <div class="skyed-audio-box__label">English</div>
                              <audio controls preload="none" src="<?php echo self::escu($a_en); ?>"></audio>
                            </div>
                          <?php endif; ?>
                          <?php if ($a_zh !== ''): ?>
                            <div class="skyed-audio-box">
                              <div class="skyed-audio-box__label">Chinese</div>
                              <audio controls preload="none" src="<?php echo self::escu($a_zh); ?>"></audio>
                            </div>
                          <?php endif; ?>
                        </div>

                        <div class="skyed-card__hint mt-3">Repeat ×2 → say 1 full sentence.</div>
                      </div>
                    </article>
                  </div>
                  <?php endforeach; ?>
                </div>
              </section>

              <section class="skyed-section mt-4">
                <div class="skyed-section__head">
                  <div>
                    <div class="skyed-section__eyebrow">Sentence pattern</div>
                    <h2 class="skyed-section__title">Sentence Practice</h2>
                  </div>
                  <div class="skyed-section__note">Keep the lines short, clear, and repeatable.</div>
                </div>

                <div class="d-flex flex-column gap-3">
                  <?php foreach ($sentences as $s):
                    $en = isset($s['en']) ? (string)$s['en'] : '';
                    $zh = isset($s['zh']) ? (string)$s['zh'] : '';
                    $a_en = isset($s['audio_en']) ? (string)$s['audio_en'] : '';
                    $a_zh = isset($s['audio_zh']) ? (string)$s['audio_zh'] : '';
                  ?>
                  <article class="card skyed-sent border-0 shadow-sm">
                    <div class="card-body">
                      <div class="skyed-sent__row">
                        <div class="skyed-sent__text">
                          <?php if ($en !== ''): ?><div class="skyed-sent__line skyed-sent__line--en"><?php echo self::esc($en); ?></div><?php endif; ?>
                          <?php if ($zh !== ''): ?><div class="skyed-sent__line skyed-sent__line--zh"><?php echo self::esc($zh); ?></div><?php endif; ?>
                        </div>
                        <div class="skyed-sent__audio">
                          <?php if ($a_en !== ''): ?>
                            <div class="skyed-audio-box">
                              <div class="skyed-audio-box__label">English</div>
                              <audio controls preload="none" src="<?php echo self::escu($a_en); ?>"></audio>
                            </div>
                          <?php endif; ?>
                          <?php if ($a_zh !== ''): ?>
                            <div class="skyed-audio-box">
                              <div class="skyed-audio-box__label">Chinese</div>
                              <audio controls preload="none" src="<?php echo self::escu($a_zh); ?>"></audio>
                            </div>
                          <?php endif; ?>
                        </div>
                      </div>
                    </div>
                  </article>
                  <?php endforeach; ?>
                </div>
              </section>

              <section class="skyed-section mt-4">
                <div class="skyed-section__head">
                  <div>
                    <div class="skyed-section__eyebrow"><?php echo self::esc($practice_family); ?></div>
                    <h2 class="skyed-section__title"><?php echo self::esc($practice_title); ?></h2>
                  </div>
                  <div class="skyed-section__note">Short, logical checks based on today’s lesson only.</div>
                </div>

                <?php if (!empty($practice_questions)): ?>
                  <div class="card border-0 shadow-sm skyed-practice">
                    <div class="card-body">
                      <div class="skyed-practice__toolbar">
                        <div class="skyed-practice__meta">
                          <div class="skyed-practice__title"><?php echo self::esc($practice_title); ?></div>
                          <div class="skyed-practice__sub"><?php echo esc_html($count_q); ?> questions · choose carefully</div>
                        </div>
                        <div class="skyed-practice__actions">
                          <button class="btn skyed-btn skyed-btn--ghost" type="button" id="<?php echo esc_attr($uid); ?>_reset">Retry</button>
                          <button class="btn skyed-btn skyed-btn--primary" type="button" id="<?php echo esc_attr($uid); ?>_submit">Check answers</button>
                        </div>
                      </div>

                      <div class="skyed-progress mt-3 mb-4"><div class="skyed-progress__bar" id="<?php echo esc_attr($uid); ?>_bar"></div></div>

                      <div id="<?php echo esc_attr($uid); ?>_app"></div>
                      <div class="mt-3" id="<?php echo esc_attr($uid); ?>_result"></div>

                      <script type="application/json" id="<?php echo esc_attr($uid); ?>_data"><?php echo wp_json_encode($practice); ?></script>
                    </div>
                  </div>

                  <script>
                  (function(){
                    const uid = <?php echo json_encode($uid); ?>;
                    const dataEl = document.getElementById(uid + "_data");
                    const app = document.getElementById(uid + "_app");
                    const resultEl = document.getElementById(uid + "_result");
                    const btn = document.getElementById(uid + "_submit");
                    const resetBtn = document.getElementById(uid + "_reset");
                    const bar = document.getElementById(uid + "_bar");
                    if (!dataEl || !app || !btn || !resetBtn) return;

                    let practice;
                    try { practice = JSON.parse(dataEl.textContent || "{}"); } catch(e){ practice = {}; }
                    const questions = practice.questions || [];
                    const answers = {};
                    const cards = [];

                    function normalizeChoice(c){
                      if (typeof c === 'string') return { text: c, img: '', subtext: '' };
                      if (c && typeof c === 'object') return { text: c.text || '', img: c.img || '', subtext: c.subtext || '' };
                      return { text: '—', img: '', subtext: '' };
                    }

                    function updateProgress(){
                      const answered = Object.keys(answers).length;
                      const pct = questions.length ? Math.round((answered / questions.length) * 100) : 0;
                      if (bar) bar.style.width = pct + '%';
                    }

                    questions.forEach((q, idx) => {
                      const card = document.createElement('article');
                      card.className = 'skyed-qcard';
                      card.dataset.index = String(idx);

                      const head = document.createElement('div');
                      head.className = 'skyed-qcard__head';

                      const num = document.createElement('div');
                      num.className = 'skyed-qcard__num';
                      num.textContent = String(idx + 1);
                      head.appendChild(num);

                      const body = document.createElement('div');
                      body.className = 'skyed-qcard__body';

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
                        if (textWrap.children.length) {
                          inner.appendChild(textWrap);
                        }

                        choiceBtn.appendChild(inner);
                        choiceBtn.onclick = () => {
                          answers[idx] = ci;
                          [...choicesWrap.querySelectorAll('.skyed-choice')].forEach(x => x.classList.remove('active'));
                          choiceBtn.classList.add('active');
                          updateProgress();
                        };
                        choicesWrap.appendChild(choiceBtn);
                      });

                      body.appendChild(choicesWrap);
                      head.appendChild(body);
                      card.appendChild(head);
                      app.appendChild(card);
                      cards.push(card);
                    });

                    function resetPractice(){
                      Object.keys(answers).forEach(k => delete answers[k]);
                      cards.forEach(card => {
                        card.querySelectorAll('.skyed-choice').forEach(btn => btn.classList.remove('active', 'correct', 'wrong'));
                      });
                      if (resultEl) resultEl.innerHTML = '';
                      updateProgress();
                    }

                    resetBtn.onclick = resetPractice;
                    btn.onclick = () => {
                      let score = 0;
                      cards.forEach((card, idx) => {
                        const q = questions[idx] || {};
                        const expected = Number(q.answer_index);
                        const chosen = (idx in answers) ? Number(answers[idx]) : -1;
                        const buttons = [...card.querySelectorAll('.skyed-choice')];
                        buttons.forEach((b, bi) => {
                          b.classList.remove('correct', 'wrong');
                          if (bi === expected) b.classList.add('correct');
                          if (bi === chosen && chosen !== expected) b.classList.add('wrong');
                        });
                        if (chosen === expected) score++;
                      });
                      const total = questions.length;
                      resultEl.innerHTML = '<div class="alert skyed-alert m-0">Score: <b>' + score + '</b> / ' + total + '</div>';
                    };

                    updateProgress();
                  })();
                  </script>
                <?php else: ?>
                  <div class="alert alert-warning">Practice data missing in payload. Re-run generation.</div>
                <?php endif; ?>
              </section>
            </section>
          </div>
        </div>
        <?php
        return ob_get_clean();
    }
}

SkyEd_Lesson_Renderer::init();
