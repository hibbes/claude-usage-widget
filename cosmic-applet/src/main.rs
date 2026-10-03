// SPDX-License-Identifier: MIT
//! COSMIC panel applet for claude-usage-widget.
//!
//! The Python daemon writes `~/.config/claude-usage-widget/conky.txt`
//! (key=value lines). This applet only reads that file: the panel shows the
//! session percentage, a click opens a popup with session and weekly bars,
//! reset times, extra usage and token counts.

use std::{collections::HashMap, path::PathBuf, sync::LazyLock, time::Duration};

use cosmic::{
    Element, Task, app,
    applet::{cosmic_panel_config::PanelAnchor, padded_control},
    cosmic_theme::Spacing,
    iced::{
        Alignment, Length, Subscription,
        widget::{column, progress_bar, row},
        window,
    },
    theme,
    widget::{Id, autosize, button, container, divider, icon, space, text},
};

const APP_ID: &str = "io.github.hibbes.CosmicAppletClaudeUsage";
const REFRESH: Duration = Duration::from_secs(30);
/// Data older than this is shown as stale (daemon writes every 60 s).
const STALE_AFTER: Duration = Duration::from_secs(600);
const ICON: &[u8] = include_bytes!("../data/robot-symbolic.svg");

static AUTOSIZE_MAIN_ID: LazyLock<Id> = LazyLock::new(|| Id::new("autosize-main"));

fn data_file() -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    home.join(".config/claude-usage-widget/conky.txt")
}

#[derive(Debug, Clone, Default)]
struct Usage {
    values: HashMap<String, String>,
    /// Age of conky.txt; None if the file is missing.
    age: Option<Duration>,
}

impl Usage {
    fn load() -> Self {
        let path = data_file();
        let Ok(content) = std::fs::read_to_string(&path) else {
            return Self::default();
        };
        let values = content
            .lines()
            .filter_map(|l| l.split_once('='))
            .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
            .collect();
        let age = std::fs::metadata(&path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.elapsed().ok());
        Self { values, age }
    }

    fn get(&self, key: &str) -> Option<&str> {
        self.values.get(key).map(String::as_str).filter(|v| !v.is_empty())
    }

    fn pct(&self, key: &str) -> Option<f32> {
        self.get(key)?.parse().ok()
    }

    fn stale(&self) -> bool {
        self.age.is_none_or(|a| a > STALE_AFTER)
    }

    /// "in 14m (09:00)" from `<prefix>_reset` and `<prefix>_resets_at`.
    fn reset_line(&self, prefix: &str) -> Option<String> {
        let rel = self.get(&format!("{prefix}_reset"));
        let abs = self
            .get(&format!("{prefix}_resets_at"))
            .and_then(|iso| iso.parse::<jiff::Timestamp>().ok())
            .map(|ts| {
                let local = ts.to_zoned(jiff::tz::TimeZone::system());
                let today = jiff::Zoned::now().date();
                if local.date() == today {
                    local.strftime("%H:%M").to_string()
                } else {
                    local.strftime("%a %d.%m. %H:%M").to_string()
                }
            });
        match (rel, abs) {
            (Some(r), Some(a)) => Some(format!("Reset in {r} ({a})")),
            (Some(r), None) => Some(format!("Reset in {r}")),
            (None, Some(a)) => Some(format!("Reset {a}")),
            (None, None) => None,
        }
    }
}

#[derive(Default)]
struct Applet {
    core: cosmic::app::Core,
    popup: Option<window::Id>,
    usage: Usage,
}

#[derive(Debug, Clone)]
enum Message {
    Tick,
    TogglePopup,
    CloseRequested(window::Id),
}

impl cosmic::Application for Applet {
    type Message = Message;
    type Executor = cosmic::SingleThreadExecutor;
    type Flags = ();
    const APP_ID: &'static str = APP_ID;

    fn init(core: cosmic::app::Core, _flags: ()) -> (Self, app::Task<Message>) {
        let applet = Self {
            core,
            usage: Usage::load(),
            ..Default::default()
        };
        (applet, Task::none())
    }

    fn core(&self) -> &cosmic::app::Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut cosmic::app::Core {
        &mut self.core
    }

    fn update(&mut self, message: Message) -> app::Task<Message> {
        match message {
            Message::Tick => self.usage = Usage::load(),
            Message::TogglePopup => {
                if let Some(id) = self.popup.take() {
                    return cosmic::surface::surface_task(
                        cosmic::surface::action::destroy_popup(id),
                    );
                }
                self.usage = Usage::load();
                return cosmic::surface::surface_task(cosmic::surface::action::app_popup(
                    |_| Default::default(),
                    |app: &mut Applet| {
                        let id = window::Id::unique();
                        app.popup = Some(id);
                        app.core.applet.get_popup_settings(
                            app.core.main_window_id().unwrap(),
                            id,
                            None,
                            None,
                            None,
                        )
                    },
                    None,
                ));
            }
            Message::CloseRequested(id) => {
                if self.popup == Some(id) {
                    self.popup = None;
                }
            }
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let horizontal = matches!(
            self.core.applet.anchor,
            PanelAnchor::Top | PanelAnchor::Bottom
        );
        let (icon_size, _) = self.core.applet.suggested_size(true);
        let mut handle = icon::from_svg_bytes(ICON);
        handle.symbolic = true;
        let glyph = icon::icon(handle).size(icon_size);

        let label = match self.usage.pct("session_pct") {
            Some(p) if !self.usage.stale() => format!("{p:.0}%"),
            Some(p) => format!("{p:.0}%?"),
            None => "–".to_string(),
        };

        let content: Element<'_, Message> = if horizontal {
            row![
                glyph,
                self.core.applet.text(label),
                container(space::vertical().height(Length::Fixed(
                    (self.core.applet.suggested_size(true).1
                        + 2 * self.core.applet.suggested_padding(true).1)
                        as f32
                )))
            ]
            .spacing(4)
            .align_y(Alignment::Center)
            .into()
        } else {
            column![glyph, self.core.applet.text(label)]
                .spacing(2)
                .align_x(Alignment::Center)
                .into()
        };

        let pad = self.core.applet.suggested_padding(true).0;
        let button = button::custom(content)
            .padding(if horizontal { [0, pad] } else { [pad, 0] })
            .on_press_down(Message::TogglePopup)
            .class(cosmic::theme::Button::AppletIcon);

        autosize::autosize(button, AUTOSIZE_MAIN_ID.clone()).into()
    }

    fn view_window(&self, _id: window::Id) -> Element<'_, Message> {
        let Spacing {
            space_xxs, space_s, ..
        } = theme::active().cosmic().spacing;
        let u = &self.usage;

        let bar = |title: &str, key: &str, reset: &str| -> Element<'_, Message> {
            let pct = u.pct(key);
            let head = row![
                text::heading(title.to_string()),
                space::horizontal(),
                text::body(pct.map_or("–".into(), |p| format!("{p:.0} %"))),
            ];
            let mut col = column![head, progress_bar(0.0..=100.0, pct.unwrap_or(0.0)).girth(6)]
                .spacing(space_xxs);
            if let Some(line) = u.reset_line(reset) {
                col = col.push(text::caption(line));
            }
            padded_control(col).into()
        };

        let info = |label: &str, key: &str| -> Option<Element<'_, Message>> {
            let value = u.get(key)?;
            Some(
                padded_control(row![
                    text::body(label.to_string()),
                    space::horizontal(),
                    text::body(value.to_string()),
                ])
                .into(),
            )
        };

        let status = match u.age {
            None => "Keine Daten: läuft der claude-usage-widget-Daemon?".to_string(),
            Some(a) if a > STALE_AFTER => format!("Veraltet: Stand vor {} min", a.as_secs() / 60),
            Some(a) if a.as_secs() < 60 => "Stand: gerade eben".to_string(),
            Some(a) => format!("Stand: vor {} min", a.as_secs() / 60),
        };

        let content = column![
            bar("Session", "session_pct", "session"),
            bar("Woche", "weekly_pct", "weekly"),
            padded_control(divider::horizontal::default()).padding([space_xxs, space_s]),
        ]
        .push_maybe(info("Extra", "extra_display"))
        .push_maybe(info("Tokens heute", "today_tokens"))
        .push_maybe(info("Tokens/min", "tokens_per_min"))
        .push(padded_control(text::caption(status)))
        .padding([8, 0])
        .width(Length::Fixed(300.0));

        self.core.applet.popup_container(content).into()
    }

    fn subscription(&self) -> Subscription<Message> {
        cosmic::iced::time::every(REFRESH).map(|_| Message::Tick)
    }

    fn on_close_requested(&self, id: window::Id) -> Option<Message> {
        Some(Message::CloseRequested(id))
    }

    fn style(&self) -> Option<cosmic::iced::theme::Style> {
        Some(cosmic::applet::style())
    }
}

fn main() -> cosmic::iced::Result {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    cosmic::applet::run::<Applet>(())
}
