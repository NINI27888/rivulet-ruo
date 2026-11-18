// 鱼的小窝 · 本地生活引擎(Pure local,无 HTTP、无外部进程)
//
// 设计目标:
//   1. 完全跑在 exe 里:状态文件 exe 同级 data/home.json,没有就以内置 seed 初始化;
//   2. 每 3 小时自动整理一次家具(移动/添置/转向),每次写一条带原因的生活事件,
//      事件里带家具 id、调整前后坐标;家具校验复用老版 house.py 的规则
//      (越界 / 骑墙 / 一格一件 / 挡门口楼梯 都不许);
//   3. 离线补算:重开程序后按真实时间补算作物成熟、工资、错过的整理周期;
//   4. 角色按作息在两层楼和院子之间活动,雇工有独立形象 key,行动写事件。
//
// 暴露给前端的命令(见 lib.rs 注册):
//   home_snapshot()            -> 场景快照(家具/角色/地块/仓库/事件/天气…)
//   home_action({ body })      -> 操作(plant/water/fertilize/harvest/sell/buy_seed/hire/fire/assign/lock/adjust_now)
//   home_status()              -> 阶段 / 天气 / 金币 / 下次整理时间
//   home_adjust_now()          -> 立即整理一次(验收演示用)
#![allow(dead_code)]

use std::collections::{BTreeMap, HashMap, VecDeque};
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::State as TauriState;

// ============================================================
// 常量:房子形状 / 家具目录 / 作物 / 作息
// ============================================================

const FLOORS: [u8; 2] = [1, 2];
const W: i32 = 12;
const H: i32 = 8;

/// 每层的房间:(层, key, 名字, x0, y0, x1, y1) —— 含头含尾,和老版 house.py 一致
const ROOMS: &[(u8, &str, &str, i32, i32, i32, i32)] = &[
    (1, "living", "客厅", 0, 0, 7, 7),
    (1, "kitchen", "厨房", 8, 0, 11, 3),
    (1, "hall", "玄关", 8, 4, 11, 7),
    (2, "bedroom", "卧室", 0, 0, 6, 7),
    (2, "study", "书房", 7, 0, 11, 7),
];

const STAIR: (i32, i32, i32, i32) = (11, 4, 11, 6); // 两层共用的楼梯口
const DOOR: (i32, i32) = (9, 7); // 一层玄关的门

/// 家具目录:(kind, 名字, 宽, 高, 只能放这几个房间)
const CATALOG: &[(&str, &str, i32, i32, &[&str])] = &[
    ("sofa", "沙发", 3, 1, &["living"]),
    ("coffee", "茶几", 2, 1, &["living"]),
    ("tv", "电视", 3, 1, &["living"]),
    ("rug", "地毯", 3, 2, &["living", "bedroom"]),
    ("dtable", "餐桌", 2, 1, &["kitchen", "living"]),
    ("table", "餐桌", 2, 1, &["kitchen", "living"]),
    ("chair", "椅子", 1, 1, &["kitchen", "living", "study", "bedroom"]),
    ("fridge", "冰箱", 1, 1, &["kitchen"]),
    ("stove", "灶台", 2, 1, &["kitchen"]),
    ("sink", "水槽", 1, 1, &["kitchen"]),
    ("shoe", "鞋柜", 1, 1, &["hall"]),
    ("plant", "绿植", 1, 1, &["living", "kitchen", "study", "bedroom", "hall"]),
    ("lamp", "落地灯", 1, 1, &["living", "bedroom", "study", "hall"]),
    ("bed", "床", 2, 3, &["bedroom"]),
    ("nstand", "床头柜", 1, 1, &["bedroom"]),
    ("closet", "衣柜", 2, 1, &["bedroom"]),
    ("desk", "书桌", 2, 1, &["study"]),
    ("shelf", "书架", 2, 1, &["study", "living", "bedroom"]),
    ("dlamp", "台灯", 1, 1, &["study", "bedroom"]),
    ("bean", "懒人沙发", 1, 1, &["bedroom", "study", "living"]),
    ("catbed", "猫窝", 1, 1, &["living", "bedroom"]),
];

const CROPS: &[(&str, &str, i64)] = &[
    ("tomato", "番茄", 2),
    ("bokchoy", "小白菜", 1),
    ("carrot", "胡萝卜", 2),
    ("sunflower", "向日葵", 3),
    ("daisy", "小雏菊", 1),
    ("potato", "土豆", 2),
    ("pumpkin", "南瓜", 3),
];

const MATURE_MIN: i64 = 120; // 兼容旧字段
/// 初代 farm.py:作物长成要几小时 / 种在哪层
const CROP_HOURS: &[(&str, f64, u8)] = &[
    ("bokchoy", 1.6, 1), ("tomato", 2.2, 1), ("pumpkin", 3.0, 1),
    ("carrot", 2.0, 1), ("potato", 2.4, 1), ("sunflower", 2.6, 2), ("daisy", 1.4, 2),
];
const GATHER_HILL: &[(&str, i64)] = &[("木头", 40), ("石头", 30), ("草药", 20), ("铁矿石", 10)];
const GATHER_SEA: &[(&str, i64)] = &[("贝壳", 34), ("海带", 34), ("细沙", 27), ("珍珠", 5)];
const ITEM_PRICE: &[(&str, i64)] = &[
    ("木头", 1), ("石头", 1), ("草药", 2), ("铁矿石", 3),
    ("贝壳", 1), ("海带", 1), ("细沙", 1), ("珍珠", 8),
    ("木板", 3), ("草药包", 6), ("珍珠项链", 30),
];
const RECIPES: &[(&str, &str, &[(&str, i64)])] = &[
    ("plank", "木板", &[("木头", 2)]),
    ("herb_pack", "草药包", &[("草药", 2)]),
    ("pearl_necklace", "珍珠项链", &[("珍珠", 2), ("细沙", 1)]),
];

const DRY_SECS: i64 = 5760;   // 1.6 小时:浇过水算湿,过了就算干
const WET_SPEED: f64 = 1.25;  // 湿土长得快
const DRY_SPEED: f64 = 0.62;  // 干土长得慢一半

/// 她一天爱待的地方:key -> (层, 格子x, 格子y, 说明)
const SPOTS: &[(&str, u8, f64, f64, &str)] = &[
    ("sleep", 2, 2.0, 2.0, "在二楼卧室睡觉"),
    ("read", 2, 8.0, 2.0, "在书房看书"),
    ("cook", 1, 9.0, 1.0, "在厨房弄吃的"),
    ("stare", 1, 2.0, 1.0, "在一楼窗边发呆"),
    ("tv", 1, 2.0, 2.0, "窝在沙发上看电视"),
    ("eat", 1, 9.0, 3.0, "在餐桌前吃饭"),
    ("water", 1, 0.0, 1.0, "在给绿植浇水"),
];

const DAY: &[(u32, u32, &str)] = &[
    (0, 7, "sleep"),
    (7, 9, "stare"),
    (9, 11, "read"),
    (11, 13, "cook"),
    (13, 15, "stare"),
    (15, 18, "read"),
    (18, 20, "eat"),
    (20, 23, "tv"),
    (23, 24, "sleep"),
];

/// 能自己挪动的小件
const MOVABLE: &[&str] = &["rug", "plant", "lamp", "dlamp", "chair", "bean", "catbed", "nstand"];
/// 能自己添置的小件:(kind, 上限)
const ADDABLE: &[(&str, usize)] = &[
    ("plant", 4),
    ("dlamp", 2),
    ("chair", 3),
    ("bean", 2),
    ("catbed", 2),
    ("lamp", 3),
    ("rug", 3),
    ("nstand", 2),
];

const ADJUST_SECS: i64 = 3 * 3600; // 三小时一次
const TZ_OFFSET: i64 = 8 * 3600; // 本机按东八区算

// ---- 生活模拟 v2:七名角色 / 雇佣 / 移动与工作 ----
const HIRE_COST: i64 = 10; // 一次性雇佣费(不是日薪)
const HIRE_SLOTS: i64 = 6; // 最多雇 3 个人(两页加一起;鲸鱼娘不算雇工)
/// (id, 名字, 形象key, 职责, 默认工作)
const ROSTER: &[(&str, &str, &str, &str, &str)] = &[
    ("jingyu", "鲸鱼娘", "whale", "主人", ""),
    ("claude", "Claude", "claude", "读书人", "read"),
    ("chatgpt", "ChatGPT", "chatgpt", "白龙少女", "read"),
    ("nini", "泥泥", "nini", "浇花员", "water"),
    ("huahua", "花花", "huahua", "温室园丁", "plant"),
    ("haihai", "海海", "haihai", "大田帮手", "harvest"),
    ("weiwei", "薇薇", "weiwei", "整理员", "tidy"),
];
/// 家具详情卡数据:(kind, 类型, 说明, 用途)
const FURN_INFO: &[(&str, &str, &str, &str)] = &[
    ("bed", "卧室家具", "铺着鼠尾草绿被子的双人床,床头靠着墙。", "睡觉、恢复体力"),
    ("nstand", "卧室家具", "床头小柜,抽屉里放着书本和水杯。", "随手放东西"),
    ("closet", "卧室家具", "两开门的木衣柜,衣服按颜色挂好。", "收纳衣物"),
    ("desk", "书房家具", "靠窗的木书桌,桌上有台灯和一摞书。", "看书、写字"),
    ("chair", "坐具", "一把结实的木椅,坐上去正好够到桌子。", "坐下休息、工作"),
    ("shelf", "书房家具", "三层木质书架,书和杂物分门别类。", "取书、收纳"),
    ("sofa", "客厅家具", "软软的布艺沙发,靠背有一点塌。", "坐着休息、看电视"),
    ("coffee", "客厅家具", "低矮的茶几,上面常放着茶杯。", "放茶点"),
    ("tv", "家用电器", "一台老式电视,屏幕白天也亮着。", "看电视"),
    ("rug", "装饰", "手织地毯,踩上去暖乎乎的。", "踩踏、布置房间"),
    ("plant", "装饰", "一盆绿植,叶子被擦得很干净。", "浇水、观赏"),
    ("lamp", "照明", "落地灯,暖黄色的光。", "照明"),
    ("dlamp", "照明", "书桌台灯,灯罩有点旧了。", "照明、看书"),
    ("dlight", "照明", "吊灯,光从头顶洒下来。", "照明"),
    ("fridge", "厨房家电", "塞得满满的冰箱,门上贴着便签。", "储存食物"),
    ("stove", "厨房家电", "双灶台,锅具挂在一旁。", "做饭"),
    ("sink", "厨房家电", "带水槽的操作台,水龙头擦得发亮。", "洗菜、洗碗"),
    ("dtable", "餐厅家具", "木质餐桌,配四把椅子。", "吃饭、聊天"),
    ("table", "餐厅家具", "木质餐桌,桌面上有细小的划痕。", "吃饭、聊天"),
    ("shoe", "玄关家具", "门口鞋柜,鞋子摆得整整齐齐。", "收纳鞋子"),
    ("catbed", "宠物用品", "猫窝,垫子中央被猫睡出一个窝。", "猫休息"),
    ("bean", "坐具", "懒人沙发,陷进去就不想起来。", "坐着发呆、休息"),
];
/// 作息 -> 想用的家具 kind
const SCHEDULE_FURN: &[(&str, &str)] = &[
    ("sleep", "bed"),
    ("read", "desk"),
    ("cook", "stove"),
    ("tv", "sofa"),
    ("eat", "dtable"),
    ("stare", "sofa"),
    ("water", "plant"),
];
/// 移动速度(格/秒)、到达后动作持续秒数
const MOVE_SPEED: f64 = 2.0;
const ACT_SECS: i64 = 5;

// ============================================================
// 时间(不引第三方 crate)
// ============================================================

pub fn now_ts() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn civil_from_days(z: i64) -> (i32, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    (
        (y + if m <= 2 { 1 } else { 0 }) as i32,
        m as u32,
        d as u32,
    )
}

fn days_from_civil(y: i32, m: u32, d: u32) -> i64 {
    let y = y as i64 - if m <= 2 { 1 } else { 0 };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m as i64 + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn local_parts(ts: i64) -> (i32, u32, u32, u32, u32, u32) {
    let t = ts + TZ_OFFSET;
    let days = t.div_euclid(86_400);
    let sod = t.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    (y, m, d, (sod / 3600) as u32, ((sod % 3600) / 60) as u32, (sod % 60) as u32)
}

fn fmt_ts(ts: i64) -> String {
    let (y, m, d, hh, mm, ss) = local_parts(ts);
    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02}", y, m, d, hh, mm, ss)
}

fn parse_ts(s: &str) -> Option<i64> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    let mut sp = s.split_whitespace();
    let date = sp.next()?;
    let time = sp.next().unwrap_or("00:00:00");
    let dp: Vec<&str> = date.split('-').collect();
    if dp.len() != 3 {
        return None;
    }
    let y: i32 = dp[0].parse().ok()?;
    let m: u32 = dp[1].parse().ok()?;
    let d: u32 = dp[2].parse().ok()?;
    let tp: Vec<&str> = time.split(':').collect();
    let hh: u32 = tp.first().and_then(|v| v.parse().ok()).unwrap_or(0);
    let mm: u32 = tp.get(1).and_then(|v| v.parse().ok()).unwrap_or(0);
    let ss: u32 = tp.get(2).and_then(|v| v.parse().ok()).unwrap_or(0);
    Some(days_from_civil(y, m, d) * 86_400 + (hh * 3600 + mm * 60 + ss) as i64 - TZ_OFFSET)
}

fn day_str(ts: i64) -> String {
    let (y, m, d, _, _, _) = local_parts(ts);
    format!("{:04}-{:02}-{:02}", y, m, d)
}

fn hour_of(ts: i64) -> u32 {
    local_parts(ts).3
}

pub fn phase_of(ts: i64) -> &'static str {
    match hour_of(ts) {
        6..=8 => "浇水",
        9..=11 => "除草",
        12..=13 => "吃饭",
        14..=16 => "播种",
        17..=19 => "发呆",
        20..=22 => "看电视",
        _ => "睡觉",
    }
}

// ============================================================
// 小随机数(不引 rand)
// ============================================================

fn seed_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0x9E3779B97F4A7C15)
}

struct Rng(u64);

impl Rng {
    fn new(seed: u64) -> Self {
        Rng(seed | 1)
    }
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            0
        } else {
            self.next() % n
        }
    }
    fn chance(&mut self, p: f64) -> bool {
        (self.next() % 1000) as f64 / 1000.0 < p
    }
    fn pick<'a, T>(&mut self, v: &'a [T]) -> Option<&'a T> {
        if v.is_empty() {
            None
        } else {
            Some(&v[self.below(v.len() as u64) as usize])
        }
    }
}

fn shuffle<T>(v: &mut [T], rng: &mut Rng) {
    if v.len() < 2 {
        return;
    }
    for i in (1..v.len()).rev() {
        let j = rng.below((i + 1) as u64) as usize;
        v.swap(i, j);
    }
}

// ============================================================
// 数据模型
// ============================================================

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct Coord {
    pub floor: u8,
    pub x: i32,
    pub y: i32,
}

#[derive(Clone, Serialize, Deserialize, Default, PartialEq, Eq, Hash)]
pub struct Cell {
    pub floor: u8,
    pub x: i32,
    pub y: i32,
}

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct Action {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub job: String,
    #[serde(default)]
    pub target_kind: String,
    #[serde(default)]
    pub target_id: String,
    #[serde(default)]
    pub phase: String,
    #[serde(default)]
    pub started_at: String,
    #[serde(default)]
    pub until: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Item {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub floor: u8,
    #[serde(default)]
    pub x: i32,
    #[serde(default)]
    pub y: i32,
    #[serde(default)]
    pub added_at: String,
    /// 朝向:north / south / east / west
    #[serde(default)]
    pub facing: String,
    /// 用户锁定后自动整理不会动它
    #[serde(default)]
    pub locked: bool,
    #[serde(default)]
    pub moved_at: String,
    #[serde(default)]
    pub used_count: i64,
    #[serde(default)]
    pub last_used_at: String,
}

fn stat80() -> i32 {
    80
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Character {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub energy: i32,
    #[serde(default = "stat80")]
    pub mood: i32,
    #[serde(default = "stat80")]
    pub full: i32,
    #[serde(default = "stat80")]
    pub clean: i32,
    #[serde(default)]
    pub task: String,
    #[serde(default)]
    pub role: String,
    /// 像素形象 key:whale / claude / chatgpt / nini / huahua / haihai / weiwei
    #[serde(default)]
    pub look: String,
    /// 自定义头像(data URL,从相册/本地图片换的)
    #[serde(default)]
    pub avatar: Option<String>,
    #[serde(default)]
    pub avatar_source: String,
    #[serde(default)]
    pub floor: u8,
    #[serde(default)]
    pub x: f64,
    #[serde(default)]
    pub y: f64,
    #[serde(default)]
    pub doing: String,
    /// 雇佣(内部管理信息,绝不写进生活状态/记忆正文)
    #[serde(default)]
    pub hired: bool,
    #[serde(default)]
    pub hire_cost: i64,
    #[serde(default)]
    pub job: String,
    #[serde(default)]
    pub action: Option<Action>,
    #[serde(default)]
    pub path: Vec<Cell>,
    #[serde(default)]
    pub path_started: String,
    #[serde(default)]
    pub facing: String,
    /// 在哪个场景:house / farm(家里或田里,同一时刻只在一边)
    #[serde(default)]
    pub place: String,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Staff {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub wage: i64,
    #[serde(default)]
    pub hired_at: String,
    #[serde(default)]
    pub look: String,
    /// 自定义头像(data URL,从相册/本地图片换的)
    #[serde(default)]
    pub avatar: Option<String>,
    #[serde(default)]
    pub avatar_source: String,
    #[serde(default)]
    pub floor: u8,
    #[serde(default)]
    pub x: f64,
    #[serde(default)]
    pub y: f64,
    #[serde(default)]
    pub task: String,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Plot {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub floor: u8,
    #[serde(default)]
    pub x: i32,
    #[serde(default)]
    pub y: i32,
    #[serde(default)]
    pub crop: Option<String>,
    #[serde(default)]
    pub planted_at: Option<String>,
    #[serde(default)]
    pub watered_at: Option<String>,
    #[serde(default)]
    pub fertilized_at: Option<String>,
    #[serde(default)]
    pub harvests: i64,
}

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct Event {
    #[serde(default)]
    pub at: String,
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub who: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub furniture_id: String,
    #[serde(default)]
    pub furniture: String,
    #[serde(default)]
    pub from: Option<Coord>,
    #[serde(default)]
    pub to: Option<Coord>,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub new_item: bool,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Weather {
    pub kind: String,
    pub name: String,
    pub icon: String,
    pub temp: i64,
}

#[derive(Clone, Serialize, Deserialize, Default)]
pub struct State {
    #[serde(default)]
    pub ver: i32,
    #[serde(default)]
    pub coins: i64,
    #[serde(default)]
    pub characters: Vec<Character>,
    #[serde(default)]
    pub staff: Vec<Staff>,
    #[serde(default)]
    pub furniture: Vec<Item>,
    #[serde(default)]
    pub plots: Vec<Plot>,
    #[serde(default)]
    pub warehouse: BTreeMap<String, i64>,
    /// 工坊物品柜: 物品名 -> 数量
    #[serde(default)]
    pub items: BTreeMap<String, i64>,
    #[serde(default)]
    pub last_stats_at: String,
    #[serde(default)]
    pub last_gather_at: String,
    #[serde(default)]
    pub seeds: BTreeMap<String, i64>,
    #[serde(default)]
    pub events: Vec<Event>,
    #[serde(default)]
    pub notes: Vec<Value>,
    #[serde(default)]
    pub tasks: Vec<Value>,
    #[serde(default)]
    pub ledger: Vec<Value>,
    #[serde(default)]
    pub last_tick: String,
    #[serde(default)]
    pub last_settle: String,
    #[serde(default)]
    pub last_adjust_at: String,
    #[serde(default)]
    pub last_staff_move: String,
    #[serde(default)]
    pub phase: String,
    #[serde(default)]
    pub character_pos: Option<Value>,
    #[serde(default)]
    pub farmer_pos: Option<Value>,
    #[serde(default)]
    pub next_id: i64,
    #[serde(default)]
    pub weather_kind: Option<String>,
    #[serde(default)]
    pub weather_name: Option<String>,
    #[serde(default)]
    pub weather_icon: Option<String>,
    #[serde(default)]
    pub weather_temp: Option<i64>,
    // ---- 生活模拟 v2 ----
    #[serde(default)]
    pub event_seq: i64,
    #[serde(default)]
    pub memory_ids: BTreeMap<String, String>,
    #[serde(default)]
    pub memory_queue: Vec<Value>,
    /// (角色:事件类型) -> 上次写入记忆库的时间戳,10 分钟内最多一条
    #[serde(default)]
    pub memory_gate: BTreeMap<String, i64>,
    #[serde(default)]
    pub memory_last_ok: String,
    #[serde(default)]
    pub memory_last_err: String,
    #[serde(default)]
    pub work_history: Vec<Value>,
    /// 初代版本搬过来的房屋布局:房间矩形 / 楼梯 / 门 / 窗 / 家具目录(含颜色、价格)
    #[serde(default)]
    pub layout: Option<Value>,
}

// ============================================================
// 引擎
// ============================================================

pub struct HomeEngine {
    pub path: PathBuf,
    pub state: State,
    last_tick_ts: i64,
}

fn cat(kind: &str) -> (&'static str, i32, i32, &'static [&'static str]) {
    for e in CATALOG {
        if e.0 == kind {
            return (e.1, e.2, e.3, e.4);
        }
    }
    ("家具", 1, 1, &[])
}

fn cat_name(kind: &str) -> &'static str {
    cat(kind).0
}

fn is_movable(kind: &str) -> bool {
    MOVABLE.contains(&kind)
}

fn room_of(floor: u8, x: i32, y: i32) -> Option<(u8, &'static str, &'static str, i32, i32, i32, i32)> {
    ROOMS
        .iter()
        .copied()
        .find(|r| r.0 == floor && r.3 <= x && x <= r.5 && r.4 <= y && y <= r.6)
}

fn room_name(floor: u8, x: i32, y: i32) -> String {
    match room_of(floor, x, y) {
        Some(r) => format!("{}F{}", r.0, r.2),
        None => format!("{}F 墙边", floor),
    }
}

fn blocked_cell(floor: u8, x: i32, y: i32) -> Option<&'static str> {
    if x >= STAIR.0 && x <= STAIR.2 && y >= STAIR.1 && y <= STAIR.3 {
        return Some("楼梯口");
    }
    if floor == 1 && (x, y) == DOOR {
        return Some("门口");
    }
    None
}

fn facing_cn(f: &str) -> &'static str {
    match f {
        "north" => "朝北",
        "south" => "朝南",
        "east" => "朝东",
        "west" => "朝西",
        _ => "朝里",
    }
}

fn crop_coef(crop: &str) -> i64 {
    CROPS.iter().find(|c| c.0 == crop).map(|c| c.2).unwrap_or(1)
}

fn crop_name(crop: &str) -> String {
    CROPS
        .iter()
        .find(|c| c.0 == crop)
        .map(|c| c.1.to_string())
        .unwrap_or_else(|| format!("未知作物({})", crop))
}

fn job_cn(job: &str) -> &'static str {
    match job {
        "water" => "浇花",
        "harvest" => "收获",
        "plant" => "播种",
        "tidy" => "整理",
        "read" => "看书",
        "sleep" => "睡觉",
        "eat" => "吃饭",
        "tv" => "看电视",
        "stare" => "发呆",
        "rest" => "休息",
        "wander" => "散步",
        _ => "空闲",
    }
}

fn anim_for_job(job: &str) -> &'static str {
    match job {
        "sleep" => "sleep",
        "read" => "read",
        "eat" => "eat",
        "tv" | "stare" | "sit" => "sit",
        "water" | "harvest" | "plant" | "tidy" => "work",
        _ => "idle",
    }
}

fn schedule_note(key: &str) -> &'static str {
    match key {
        "sleep" => "卧室睡觉",
        "read" => "书房看书",
        "cook" => "厨房做饭",
        "tv" => "客厅看电视",
        "eat" => "餐桌吃饭",
        "stare" => "窗边发呆",
        "water" => "给绿植浇水",
        _ => "待着",
    }
}

fn schedule_anim(key: &str) -> &'static str {
    match key {
        "sleep" => "sleep",
        "read" => "read",
        "eat" => "eat",
        "cook" => "work",
        "water" => "work",
        _ => "sit",
    }
}

/// 是否写进统一记忆库的历史事件(雇佣/解雇/锁定属于内部管理,不写)
fn memory_syncable(kind: &str) -> bool {
    // 只写「值得记」的:收获 / 播种 / 买卖 / 雇佣关系变化 / 家具变化。
    // 普通移动、浇一次水、休息、工作开始等只进本地生活日记,不刷记忆库。
    matches!(
        kind,
        "plant" | "harvest" | "sell" | "hire" | "fire"
            | "furniture_add" | "furniture_move" | "furniture_rotate"
    )
}

fn event_category(kind: &str) -> &'static str {
    match kind {
        "plant" | "water" | "harvest" | "fertilize" | "sell" | "seed" | "weed" => "farm",
        "furniture_move" | "furniture_add" | "furniture_rotate" | "furniture_lock"
        | "furniture_unlock" => "furniture",
        "hire" | "fire" | "assign_work" | "assign" => "manage",
        "wage" | "unpaid" => "economy",
        _ => "life",
    }
}

fn furn_color(kind: &str) -> &'static str {
    match kind {
        "sofa" => "#4A5A78", "coffee" => "#6B5A44", "tv" => "#2E3238",
        "rug" => "#5C4657", "dtable" => "#6B5A44", "chair" => "#7A6448",
        "fridge" => "#9AA3AD", "stove" => "#3A3F46", "sink" => "#8A949E",
        "shoe" => "#4E4436", "plant" => "#3F6B47", "lamp" => "#C8A24A",
        "bed" => "#586A8C", "nstand" => "#5B4B3A", "closet" => "#4C4038",
        "desk" => "#6B5A44", "shelf" => "#4A3E34", "dlamp" => "#C8A24A",
        "bean" => "#6A4E6E", "catbed" => "#7A5548",
        _ => "#8B7A64",
    }
}

fn furn_price(kind: &str) -> i64 {
    match kind {
        "bed" => 30,
        "sofa" => 22,
        "closet" => 18,
        "desk" => 20,
        "shelf" => 16,
        "dtable" | "table" => 18,
        "tv" => 25,
        "fridge" => 24,
        "stove" => 20,
        "sink" => 14,
        "lamp" | "dlamp" => 10,
        "plant" | "chair" => 8,
        "rug" => 12,
        "nstand" => 10,
        "catbed" => 8,
        "bean" | "coffee" => 12,
        "shoe" => 10,
        _ => 12,
    }
}

fn furn_info(kind: &str) -> (&'static str, &'static str, &'static str) {
    for e in FURN_INFO {
        if e.0 == kind {
            return (e.1, e.2, e.3);
        }
    }
    ("家具", "一件普通家具。", "使用")
}

fn look_for_index(i: usize) -> &'static str {
    const LOOKS: [&str; 8] = ["vivi", "claude", "gork", "huahua", "kanhai", "chatgpt", "bean", "momo"];
    LOOKS[i % LOOKS.len()]
}

fn look_for_id(id: &str, index: usize) -> String {
    match id {
        "jingyu" => "whale".to_string(),
        "kanhai" => "sea".to_string(),
        _ => look_for_index(index).to_string(),
    }
}

impl HomeEngine {
    // ---------------- 打开 / 存盘 ----------------

    pub fn open_default() -> Self {
        Self::open(default_state_path())
    }

    pub fn open(path: PathBuf) -> Self {
        let mut state: State = match fs::read_to_string(&path) {
            Ok(txt) => serde_json::from_str(&txt).unwrap_or_else(|_| seed_state()),
            Err(_) => seed_state(),
        };
        let mut eng = HomeEngine {
            path,
            state,
            last_tick_ts: 0,
        };
        eng.normalize();
        if !eng.path.exists() {
            let _ = eng.save();
        }
        // 启动即补算一轮:离线跨过的周期、工资、作物成熟
        eng.tick_at(now_ts());
        eng
    }

    fn normalize(&mut self) {
        let now = now_ts();

        if self.state.ver == 0 {
            self.state.ver = 1;
        }
        // 角色名册由 ensure_roster() 统一生成(7 名)

        // 老数据只有 character_pos,没有逐角色坐标 —— 从这里补
        let pos = self.state.character_pos.clone();
        for (i, c) in self.state.characters.iter_mut().enumerate() {
            if c.floor != 1 && c.floor != 2 {
                c.floor = 1;
            }
            if let Some(p) = &pos {
                if i == 0 && c.floor == 1 && c.x == 0.0 && c.y == 0.0 {
                    c.floor = p.get("floor").and_then(|v| v.as_u64()).unwrap_or(1) as u8;
                    c.x = p.get("x").and_then(|v| v.as_f64()).unwrap_or(2.5);
                    c.y = p.get("y").and_then(|v| v.as_f64()).unwrap_or(1.5);
                    c.doing = p
                        .get("doing")
                        .and_then(|v| v.as_str())
                        .unwrap_or("stare")
                        .to_string();
                }
            }
            if c.look.is_empty() {
                c.look = look_for_id(&c.id, i);
            }
            if c.avatar_source.is_empty() {
                c.avatar_source = if c.avatar.is_some() { "album".into() } else { "pixel".into() };
            }
            if c.energy == 0 {
                c.energy = 80;
            }
            if c.floor != 1 && c.floor != 2 {
                c.floor = 1;
            }
        }

        // 雇工:补位置和形象
        for i in 0..self.state.staff.len() {
            let (look, floor, x, y) = {
                let s = &self.state.staff[i];
                (
                    if s.look.is_empty() {
                        look_for_index(i).to_string()
                    } else {
                        s.look.clone()
                    },
                    if s.floor == 1 || s.floor == 2 { s.floor } else { 1 },
                    if s.x >= 0.0 { s.x } else { 2.0 + i as f64 },
                    if s.y >= 0.0 { s.y } else { 4.0 },
                )
            };
            let s = &mut self.state.staff[i];
            s.look = look;
            s.floor = floor;
            s.x = x;
            s.y = y;
            if s.wage <= 0 {
                s.wage = 10;
            }
            if s.role.is_empty() {
                s.role = "帮手".to_string();
            }
            if s.avatar_source.is_empty() {
                s.avatar_source = if s.avatar.is_some() { "album".into() } else { "pixel".into() };
            }
        }

        // 家具:补朝向 / id / 时间
        let mut max_num = 0i64;
        for i in 0..self.state.furniture.len() {
            let (id, facing, added) = {
                let f = &self.state.furniture[i];
                (
                    f.id.clone(),
                    if f.facing.is_empty() { "south".to_string() } else { f.facing.clone() },
                    if f.added_at.is_empty() { fmt_ts(now) } else { f.added_at.clone() },
                )
            };
            if let Some(num) = id.trim_start_matches(|c: char| !c.is_ascii_digit()).parse::<i64>().ok() {
                if num > max_num {
                    max_num = num;
                }
            }
            let f = &mut self.state.furniture[i];
            if f.id.is_empty() {
                max_num += 1;
                f.id = format!("item{}", max_num);
            }
            f.facing = facing;
            f.added_at = added;
        }

        // 地块:补楼层次序(旧数据 id 形如 p1_2_2)
        for p in self.state.plots.iter_mut() {
            if p.floor != 1 && p.floor != 2 {
                p.floor = 1;
            }
            if p.x < 0 || p.x >= W || p.y < 0 || p.y >= H {
                p.x = p.x.clamp(0, W - 1);
                p.y = p.y.clamp(0, H - 1);
            }
        }

        // 5) 七名角色:鲸鱼娘初始拥有,其余六名需要 10 金币雇佣
        self.ensure_roster();
        // 名额上限:最多 3 个雇工,多出来的退回未雇佣
        let mut kept = 0i64;
        for c in self.state.characters.iter_mut() {
            if c.hired && c.id != "jingyu" {
                if kept < HIRE_SLOTS {
                    kept += 1;
                } else {
                    c.hired = false;
                    c.place = "house".to_string();
                    c.action = None;
                    c.path.clear();
                }
            }
        }

        // 6) 初代布局(房间/楼梯/门/窗/家具目录):老状态没有就补默认
        if self.state.layout.is_none() {
            self.state.layout = Some(Self::default_layout());
        }

        if self.state.next_id < max_num + 1 {
            self.state.next_id = max_num + 1;
        }
        if self.state.last_settle.is_empty() {
            self.state.last_settle = day_str(now);
        }
        if self.state.last_adjust_at.is_empty() {
            self.state.last_adjust_at = fmt_ts(now);
        }
        if self.state.last_tick.is_empty() {
            self.state.last_tick = fmt_ts(now);
        }
        if self.state.phase.is_empty() {
            self.state.phase = phase_of(now).to_string();
        }
        if self.state.events.len() > 400 {
            let keep = self.state.events.split_off(self.state.events.len() - 400);
            self.state.events = keep;
        }
    }

    pub fn save(&self) -> Result<(), String> {
        if let Some(dir) = self.path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let txt = serde_json::to_string_pretty(&self.state)
            .map_err(|e| format!("序列化小窝状态失败:{}", e))?;
        let tmp = self.path.with_extension("json.tmp");
        fs::write(&tmp, txt).map_err(|e| format!("写小窝状态失败:{}", e))?;
        fs::rename(&tmp, &self.path).map_err(|e| format!("替换小窝状态失败:{}", e))?;
        Ok(())
    }

    // ---------------- 校验 / 空位 ----------------

    fn occupied(&self, floor: u8, skip: Option<&str>) -> Vec<(i32, i32)> {
        let mut out = Vec::new();
        for it in &self.state.furniture {
            if it.floor != floor {
                continue;
            }
            if let Some(s) = skip {
                if it.id == s {
                    continue;
                }
            }
            let (_n, w, h, _r) = cat(&it.kind);
            for dy in 0..h {
                for dx in 0..w {
                    out.push((it.x + dx, it.y + dy));
                }
            }
        }
        out
    }

    fn can_place(&self, kind: &str, floor: u8, x: i32, y: i32, skip: Option<&str>) -> Result<(), String> {
        if !FLOORS.contains(&floor) {
            return Err("楼层不对".into());
        }
        let (_name, w, h, rooms) = cat(kind);
        let occ = self.occupied(floor, skip);
        for dy in 0..h {
            for dx in 0..w {
                let cx = x + dx;
                let cy = y + dy;
                if cx < 0 || cx >= W || cy < 0 || cy >= H {
                    return Err(format!("{} 伸到墙外去了", cat_name(kind)));
                }
                let room = match room_of(floor, cx, cy) {
                    Some(r) => r,
                    None => return Err(format!("{} 压到墙上了", cat_name(kind))),
                };
                if !rooms.is_empty() && !rooms.contains(&room.1) {
                    return Err(format!("{} 不该放在{}", cat_name(kind), room.2));
                }
                if let Some(bad) = blocked_cell(floor, cx, cy) {
                    return Err(format!("{} 挡着{}了", cat_name(kind), bad));
                }
                if occ.contains(&(cx, cy)) {
                    return Err("那儿已经有东西了,一格只能放一件".into());
                }
            }
        }
        Ok(())
    }

    fn free_spots(&self, kind: &str, floor: u8, skip: Option<&str>) -> Vec<(i32, i32)> {
        let (_n, w, h, _r) = cat(kind);
        let mut out = Vec::new();
        for y in 0..=(H - h) {
            for x in 0..=(W - w) {
                if self.can_place(kind, floor, x, y, skip).is_ok() {
                    out.push((x, y));
                }
            }
        }
        out
    }

    fn free_cell(&self, floor: u8, rng: &mut Rng) -> Option<(f64, f64)> {
        let occ = self.occupied(floor, None);
        let mut cells = Vec::new();
        for y in 0..H {
            for x in 0..W {
                if x <= 0 || x >= W - 1 {
                    continue;
                }
                if occ.contains(&(x, y)) || blocked_cell(floor, x, y).is_some() {
                    continue;
                }
                if room_of(floor, x, y).is_none() {
                    continue;
                }
                cells.push((x as f64 + 0.5, y as f64 + 0.5));
            }
        }
        if cells.is_empty() {
            None
        } else {
            Some(cells[rng.below(cells.len() as u64) as usize])
        }
    }

    // ---------------- 事件 ----------------

    fn push_event(&mut self, ev: Event) {
        // 去重:最近 3 条同类型同文本同家具的不重复记
        let dup = self
            .state
            .events
            .iter()
            .rev()
            .take(20)
            .any(|e| e.text == ev.text && e.at == ev.at && e.kind == ev.kind);
        if dup {
            return;
        }
        let queued = ev.clone();
        self.state.events.push(ev);
        if self.state.events.len() > 400 {
            let cut = self.state.events.len() - 400;
            self.state.events.drain(0..cut);
        }
        // 真实生活事件 -> 统一记忆库(断网时进 memory_queue)
        self.queue_life_event(&queued);
    }

    fn note(&mut self, what: &str, why: &str, who: &str, id: &str) {
        let dup = self.state.notes.iter().take(3).any(|n| {
            n.get("what").and_then(|v| v.as_str()) == Some(what)
                && n.get("why").and_then(|v| v.as_str()) == Some(why)
        });
        if dup {
            return;
        }
        self.state.notes.insert(
            0,
            json!({
                "t": fmt_ts(now_ts()),
                "what": what,
                "why": why,
                "who": who,
                "id": id
            }),
        );
        self.state.notes.truncate(60);
    }

    fn who_main(&self) -> String {
        self.state
            .characters
            .first()
            .map(|c| c.name.clone())
            .unwrap_or_else(|| "鲸鱼娘".to_string())
    }

    // ---------------- 主循环 ----------------

    pub fn maybe_tick(&mut self) {
        let now = now_ts();
        if now - self.last_tick_ts >= 1 {
            self.tick_at(now);
        }
    }

    pub fn tick_at(&mut self, now: i64) -> Vec<Event> {
        let before = self.state.events.len();
        self.update_weather(now);
        self.advance_motion(now);
        self.finish_actions(now);
        self.plan_characters(now);
        self.maybe_adjust(now);
        self.decay_stats(now);
        self.sync_memory();
        self.state.phase = phase_of(now).to_string();
        self.state.last_tick = fmt_ts(now);
        self.last_tick_ts = now;
        let _ = self.save();
        self.state.events[before.min(self.state.events.len())..].to_vec()
    }

    /// 能量之外的三个生活指标: 心情/饱腹/清洁。每 15 分钟结算一次。
    fn decay_stats(&mut self, now: i64) {
        if self.state.last_stats_at.is_empty() {
            self.state.last_stats_at = fmt_ts(now);
            return;
        }
        let last = parse_ts(&self.state.last_stats_at).unwrap_or(now);
        let mins = (now - last) / 60;
        if mins < 15 {
            return;
        }
        let steps = (mins / 15).min(40) as i32;
        self.state.last_stats_at = fmt_ts(now);
        for c in self.state.characters.iter_mut() {
            if c.id != "jingyu" && !c.hired {
                continue;
            }
            c.full = (c.full - steps).max(0);
            c.clean = (c.clean - (steps / 2).max(1)).max(0);
            if c.full < 35 || c.clean < 35 {
                c.mood = (c.mood - steps).max(0);
            } else if c.mood < 80 {
                c.mood = (c.mood + (steps / 2).max(1)).min(100);
            }
            if c.doing == "sleep" {
                c.mood = (c.mood + steps).min(100);
            }
        }
    }

    fn settle_wages(&mut self, now: i64) {
        let today = day_str(now);
        if self.state.last_settle == today {
            return;
        }
        let last = self.state.last_settle.clone();
        if last.is_empty() {
            self.state.last_settle = today;
            return;
        }
        let last_days = parse_ts(&format!("{} 00:00:00", last))
            .map(|t| t.div_euclid(86_400))
            .unwrap_or(now.div_euclid(86_400));
        let today_days = days_from_civil(
            local_parts(now).0,
            local_parts(now).1,
            local_parts(now).2,
        );
        let missed = (today_days - last_days).clamp(0, 7);
        for _ in 0..missed {
            for i in 0..self.state.staff.len() {
                let (name, wage) = {
                    let s = &self.state.staff[i];
                    (s.name.clone(), s.wage)
                };
                if wage <= 0 {
                    continue;
                }
                if self.state.coins >= wage {
                    self.state.coins -= wage;
                    self.state.ledger.push(json!({
                        "at": fmt_ts(now), "type": "wage", "amount": -wage,
                        "note": format!("给 {} 发工资", name),
                        "balance": self.state.coins
                    }));
                    self.push_event(Event {
                        at: fmt_ts(now),
                        kind: "wage".into(),
                        text: format!("给 {} 发了 {} 金币工资", name, wage),
                        who: self.who_main(),
                        source: "home".into(),
                        ..Default::default()
                    });
                } else {
                    self.state.ledger.push(json!({
                        "at": fmt_ts(now), "type": "unpaid", "amount": 0,
                        "note": format!("钱不够,欠 {} 一天工资 {}", name, wage),
                        "balance": self.state.coins
                    }));
                    self.push_event(Event {
                        at: fmt_ts(now),
                        kind: "unpaid".into(),
                        text: format!("钱不够,欠 {} 一天工资", name),
                        who: self.who_main(),
                        source: "home".into(),
                        ..Default::default()
                    });
                }
            }
        }
        self.state.last_settle = today;
    }

    fn update_weather(&mut self, now: i64) {
        let (y, m, d, _, _, _) = local_parts(now);
        let mut rng = Rng::new(((y as i64 * 10_000 + m as i64 * 100 + d as i64) as u64) * 2654435761);
        let kinds = [
            ("sunny", "晴", "☀"),
            ("cloudy", "多云", "☁"),
            ("rain", "小雨", "☂"),
            ("windy", "起风", "≈"),
        ];
        // 冬天更少晴天
        let idx = if m == 12 || m <= 2 {
            (rng.below(2) + 1) as usize
        } else {
            rng.below(kinds.len() as u64) as usize
        };
        let (kind, name, icon) = kinds[idx.min(kinds.len() - 1)];
        let base = match m {
            12 | 1 | 2 => 4,
            3..=5 => 16,
            6..=8 => 26,
            9..=11 => 18,
            _ => 18,
        };
        let temp = base + rng.below(7) as i64 - 3;
        self.state.weather_kind = Some(kind.to_string());
        self.state.weather_name = Some(name.to_string());
        self.state.weather_icon = Some(icon.to_string());
        self.state.weather_temp = Some(temp);
    }

    fn spot_for_key(key: &str) -> (u8, f64, f64, &'static str) {
        for (k, f, x, y, text) in SPOTS {
            if *k == key {
                return (*f, *x, *y, text);
            }
        }
        (1, 2.0, 1.0, "在一楼窗边发呆")
    }

    fn day_key(hour: u32) -> &'static str {
        for (a, b, key) in DAY {
            if *a <= hour && hour < *b {
                return key;
            }
        }
        "stare"
    }

    fn update_characters(&mut self, now: i64) {
        let hour = hour_of(now);
        for i in 0..self.state.characters.len() {
            // 第二个人晚两小时作息,看起来不是复制人
            let h = (hour + (i as u32) * 2) % 24;
            let key = Self::day_key(h);
            let (floor, x, y, text) = Self::spot_for_key(key);
            let (changed, old_floor, old_x, old_y, name) = {
                let c = &self.state.characters[i];
                (c.doing != key, c.floor, c.x, c.y, c.name.clone())
            };
            if !changed {
                continue;
            }
            let old_room = room_name(old_floor, old_x as i32, old_y as i32);
            let new_room = room_name(floor, x as i32, y as i32);
            let c = &mut self.state.characters[i];
            c.floor = floor;
            c.x = x;
            c.y = y;
            c.doing = key.to_string();
            c.task = match key {
                "sleep" => "睡觉",
                "read" => "看书",
                "cook" => "做饭",
                "tv" => "看电视",
                "eat" => "吃饭",
                "water" => "浇花",
                _ => "发呆",
            }
            .to_string();
            if key == "sleep" {
                c.energy = (c.energy + 2).min(100);
            } else if key == "eat" {
                c.energy = (c.energy + 8).min(100);
            } else {
                c.energy = (c.energy - 1).max(5);
            }
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "character_move".into(),
                text: format!("{}从{}走到{}{}", name, old_room, new_room, text),
                who: name,
                source: "home".into(),
                ..Default::default()
            });
        }
    }

    fn update_staff(&mut self, now: i64) {
        let last = parse_ts(&self.state.last_staff_move).unwrap_or(0);
        if now - last < 600 {
            return;
        }
        self.state.last_staff_move = fmt_ts(now);
        if self.state.staff.is_empty() {
            return;
        }
        let mut rng = Rng::new(seed_now());
        let n = self.state.staff.len().min(2);
        for _ in 0..n {
            let i = rng.below(self.state.staff.len() as u64) as usize;
            let other_floor = if self.state.staff[i].floor == 1 { 2 } else { 1 };
            let floor = if rng.chance(0.45) { other_floor } else { self.state.staff[i].floor };
            let cell = self.free_cell(floor, &mut rng);
            let (ox, oy, of) = (self.state.staff[i].x, self.state.staff[i].y, self.state.staff[i].floor);
            let name = self.state.staff[i].name.clone();
            let (nx, ny) = match cell {
                Some(c) => c,
                None => continue,
            };
            if (nx - ox).abs() < 0.1 && (ny - oy).abs() < 0.1 && floor == of {
                continue;
            }
            let old_room = room_name(of, ox as i32, oy as i32);
            let new_room = room_name(floor, nx as i32, ny as i32);
            {
                let s = &mut self.state.staff[i];
                s.floor = floor;
                s.x = nx;
                s.y = ny;
                s.task = if floor == 2 { "整理二楼".to_string() } else { "收拾客厅".to_string() };
            }
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "staff_move".into(),
                text: format!("{}从{}走到{}", name, old_room, new_room),
                who: name,
                source: "home".into(),
                ..Default::default()
            });
        }
    }

    fn fertilized(&self, p: &Plot) -> bool {
        p.fertilized_at.is_some()
    }

    fn crop_hours(crop: &str) -> f64 {
        CROP_HOURS
            .iter()
            .find(|c| c.0 == crop)
            .map(|c| c.1)
            .unwrap_or(2.0)
    }

    fn is_wet(&self, p: &Plot, now: i64) -> bool {
        match p.watered_at.as_deref().and_then(parse_ts) {
            Some(t) => now - t < DRY_SECS,
            None => false,
        }
    }

    fn is_dry(&self, p: &Plot, now: i64) -> bool {
        !self.is_wet(p, now)
    }

    fn progress(&self, p: &Plot, now: i64) -> f64 {
        let crop = match &p.crop {
            Some(c) => c,
            None => return 0.0,
        };
        let planted = match p.planted_at.as_deref().and_then(parse_ts) {
            Some(t) => t,
            None => return 0.0,
        };
        let mut span = (Self::crop_hours(crop) * 3600.0).max(600.0);
        if self.fertilized(p) {
            span *= 0.82;
        }
        let speed = if self.is_wet(p, now) { WET_SPEED } else { DRY_SPEED };
        (((now - planted).max(0) as f64) * speed / span).clamp(0.0, 1.0)
    }

    fn mature(&self, p: &Plot, now: i64) -> bool {
        self.progress(p, now) >= 1.0
    }

    fn crop_stage(&self, p: &Plot, now: i64) -> i32 {
        let r = self.progress(p, now);
        if r >= 1.0 { 2 } else if r >= 0.36 { 1 } else { 0 }
    }

    fn left_minutes(&self, p: &Plot, now: i64) -> i64 {
        let crop = match &p.crop {
            Some(c) => c,
            None => return 0,
        };
        let planted = match p.planted_at.as_deref().and_then(parse_ts) {
            Some(t) => t,
            None => return 0,
        };
        let mut span = (Self::crop_hours(crop) * 3600.0).max(600.0);
        if self.fertilized(p) {
            span *= 0.82;
        }
        let speed = if self.is_wet(p, now) { WET_SPEED } else { DRY_SPEED };
        let got = (((now - planted).max(0) as f64) * speed).min(span);
        (((span - got) / speed) / 60.0).ceil() as i64
    }

    fn farm_step(&mut self, now: i64) {
        let phase = phase_of(now);
        let mut rng = Rng::new(seed_now());
        let who = self.who_main();

        // 下雨:露天地块自动浇水(温室不算)
        if self.state.weather_kind.as_deref() == Some("rain") {
            for p in self.state.plots.iter_mut() {
                if p.floor == 1 && p.crop.is_some() && p.watered_at.is_none() {
                    p.watered_at = Some(fmt_ts(now));
                }
            }
        }

        if phase == "睡觉" {
            return;
        }

        // 收获优先
        if let Some(idx) = self.state.plots.iter().position(|p| self.mature(p, now)) {
            let (crop, floor) = {
                let p = &self.state.plots[idx];
                (p.crop.clone().unwrap_or_default(), p.floor)
            };
            let cname = crop_name(&crop);
            {
                let p = &mut self.state.plots[idx];
                p.crop = None;
                p.planted_at = None;
                p.watered_at = None;
                p.fertilized_at = None;
                p.harvests += 1;
            }
            let cnt = self.state.warehouse.get(&cname).copied().unwrap_or(0) + 1;
            self.state.warehouse.insert(cname.clone(), cnt);
            self.state.ledger.push(json!({
                "at": fmt_ts(now), "type": "harvest", "amount": 0,
                "note": format!("收获 {} ×1", cname), "balance": self.state.coins
            }));
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "harvest".into(),
                text: format!(
                    "{}在{}收了 1 份{},放进仓库",
                    who,
                    if floor == 2 { "二楼温室" } else { "一楼大田" },
                    cname
                ),
                who,
                source: "farm".into(),
                ..Default::default()
            });
            return;
        }

        match phase {
            "浇水" => {
                if let Some(idx) = self
                    .state
                    .plots
                    .iter()
                    .position(|p| p.crop.is_some() && p.watered_at.is_none())
                {
                    let cname = crop_name(self.state.plots[idx].crop.as_deref().unwrap_or(""));
                    self.state.plots[idx].watered_at = Some(fmt_ts(now));
                    self.push_event(Event {
                        at: fmt_ts(now),
                        kind: "water".into(),
                        text: format!("{}给{}浇了水", who, cname),
                        who,
                        source: "farm".into(),
                        ..Default::default()
                    });
                }
            }
            "播种" => {
                if let Some(idx) = self.state.plots.iter().position(|p| p.crop.is_none()) {
                    if self.state.coins >= 3 {
                        let crop = CROPS[rng.below(CROPS.len() as u64) as usize].0.to_string();
                        let cname = crop_name(&crop);
                        self.state.coins -= 3;
                        {
                            let p = &mut self.state.plots[idx];
                            p.crop = Some(crop);
                            p.planted_at = Some(fmt_ts(now));
                            p.watered_at = None;
                            p.fertilized_at = None;
                        }
                        self.state.ledger.push(json!({
                            "at": fmt_ts(now), "type": "seed", "amount": -3,
                            "note": format!("买{}种子", cname), "balance": self.state.coins
                        }));
                        self.push_event(Event {
                            at: fmt_ts(now),
                            kind: "plant".into(),
                            text: format!("{}在{}种了{}", who, if self.state.plots[idx].floor == 2 { "温室" } else { "大田" }, cname),
                            who,
                            source: "farm".into(),
                            ..Default::default()
                        });
                    }
                }
            }
            "除草" => {
                if rng.chance(0.25) {
                    self.push_event(Event {
                        at: fmt_ts(now),
                        kind: "weed".into(),
                        text: format!("{}把田里的杂草拔了", who),
                        who,
                        source: "farm".into(),
                        ..Default::default()
                    });
                }
            }
            _ => {}
        }
    }

    // ---------------- 每三小时:家具自主调整 ----------------

    fn maybe_adjust(&mut self, now: i64) {
        let last = parse_ts(&self.state.last_adjust_at).unwrap_or(now);
        if now - last < ADJUST_SECS {
            return;
        }
        let periods = (now - last) / ADJUST_SECS;
        if periods <= 0 {
            return;
        }
        // 离线跨多个周期:最多补做 2 次,避免一次性把屋子折腾乱
        let runs = periods.clamp(1, 2) as usize;
        for _ in 0..runs {
            self.adjust_once(now, "整理");
        }
        // 补算后把下次到期点对齐到当前,避免同一周期重复触发
        self.state.last_adjust_at = fmt_ts(now);
        if periods > 1 {
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "adjust_catchup".into(),
                text: format!(
                    "离线期间跨了 {} 个三小时周期,补做了 {} 次家具整理,其余周期的变化合并处理。",
                    periods, runs
                ),
                who: self.who_main(),
                source: "home".into(),
                ..Default::default()
            });
        }
    }

    fn adjust_once(&mut self, now: i64, tag: &str) -> Option<Event> {
        let mut rng = Rng::new(seed_now() ^ (now as u64) ^ ((self.state.events.len() as u64) << 7));
        let roll = rng.below(100);
        let mut ev: Option<Event> = None;

        if roll < 30 {
            ev = self.adjust_add(now, &mut rng);
        }
        if ev.is_none() && roll < 48 {
            ev = self.adjust_rotate(now, &mut rng);
        }
        if ev.is_none() {
            ev = self.adjust_move(now, &mut rng);
        }
        if ev.is_none() {
            ev = self.adjust_add(now, &mut rng);
        }
        if ev.is_none() {
            let e = Event {
                at: fmt_ts(now),
                kind: "room_check".into(),
                text: format!(
                    "{}转了一圈,看了看屋里的布置,觉得现在这样挺好,这次{}没动家具。",
                    self.who_main(),
                    tag
                ),
                reason: "屋里已经比较整齐,不用大动,先把生活节奏放慢一点。".into(),
                who: self.who_main(),
                source: "home".into(),
                ..Default::default()
            };
            self.push_event(e.clone());
            return Some(e);
        }

        let e = ev.unwrap();
        let what = if e.new_item {
            format!("添了{}", e.furniture)
        } else if e.kind == "furniture_rotate" {
            format!("转了{}", e.furniture)
        } else {
            format!("挪了{}", e.furniture)
        };
        self.push_event(e.clone());
        self.note(&what, &e.reason, &e.who, &e.furniture_id);
        Some(e)
    }

    fn adjust_move(&mut self, now: i64, rng: &mut Rng) -> Option<Event> {
        let mut pool: Vec<Item> = self
            .state
            .furniture
            .iter()
            .filter(|f| is_movable(&f.kind) && !f.locked)
            .cloned()
            .collect();
        if pool.is_empty() {
            return None;
        }
        shuffle(&mut pool, rng);
        for it in pool {
            let mut spots = self.free_spots(&it.kind, it.floor, Some(&it.id));
            if spots.is_empty() {
                continue;
            }
            shuffle(&mut spots, rng);
            let (nx, ny) = spots[0];
            if nx == it.x && ny == it.y {
                continue;
            }
            let from = Coord { floor: it.floor, x: it.x, y: it.y };
            let to = Coord { floor: it.floor, x: nx, y: ny };
            let name = cat_name(&it.kind).to_string();
            let room_from = room_name(it.floor, it.x, it.y);
            let room_to = room_name(it.floor, nx, ny);
            let reason = self.reason_move(&it, &room_to);
            let who = self.who_main();
            let nows = fmt_ts(now);
            let mut applied = false;
            if let Some(pos) = self.state.furniture.iter().position(|f| f.id == it.id) {
                let f = &mut self.state.furniture[pos];
                f.x = nx;
                f.y = ny;
                f.moved_at = nows.clone();
                applied = true;
            }
            if !applied {
                continue;
            }
            let text = format!(
                "{}把{}从{}（{},{}）挪到{}（{},{}）：{}",
                who, name, room_from, it.x, it.y, room_to, nx, ny, reason
            );
            return Some(Event {
                at: nows,
                kind: "furniture_move".into(),
                text,
                who,
                source: "home".into(),
                furniture_id: it.id,
                furniture: name,
                from: Some(from),
                to: Some(to),
                reason,
                new_item: false,
            });
        }
        None
    }

    fn adjust_add(&mut self, now: i64, rng: &mut Rng) -> Option<Event> {
        let mut kinds: Vec<&str> = ADDABLE.iter().map(|k| k.0).collect();
        shuffle(&mut kinds, rng);
        for kind in kinds {
            let cap = ADDABLE.iter().find(|k| k.0 == kind).map(|k| k.1).unwrap_or(2);
            let count = self
                .state
                .furniture
                .iter()
                .filter(|f| f.kind == kind)
                .count();
            if count >= cap {
                continue;
            }
            let mut spots: Vec<(u8, i32, i32)> = Vec::new();
            for fl in FLOORS {
                let mut sp = self.free_spots(kind, fl, None);
                shuffle(&mut sp, rng);
                for (x, y) in sp {
                    spots.push((fl, x, y));
                }
            }
            if spots.is_empty() {
                continue;
            }
            shuffle(&mut spots, rng);
            let (floor, x, y) = spots[0];
            let name = cat_name(kind).to_string();
            let room = room_name(floor, x, y);
            let reason = self.reason_add(kind, &room);
            let nows = fmt_ts(now);
            let id = format!("{}{}", kind, self.next_item_num());
            self.state.furniture.push(Item {
                id: id.clone(),
                kind: kind.to_string(),
                floor,
                x,
                y,
                added_at: nows.clone(),
                facing: "south".into(),
                locked: false,
                moved_at: nows.clone(),
                used_count: 0,
                last_used_at: String::new(),
            });
            let who = self.who_main();
            let text = format!(
                "{}在{}（{},{}）添了一件{}：{}",
                who, room, x, y, name, reason
            );
            return Some(Event {
                at: nows,
                kind: "furniture_add".into(),
                text,
                who,
                source: "home".into(),
                furniture_id: id,
                furniture: name,
                from: None,
                to: Some(Coord { floor, x, y }),
                reason,
                new_item: true,
            });
        }
        None
    }

    fn adjust_rotate(&mut self, now: i64, rng: &mut Rng) -> Option<Event> {
        let mut pool: Vec<Item> = self
            .state
            .furniture
            .iter()
            .filter(|f| is_movable(&f.kind) && !f.locked)
            .cloned()
            .collect();
        if pool.is_empty() {
            return None;
        }
        shuffle(&mut pool, rng);
        let it = &pool[0];
        let facings = ["north", "south", "east", "west"];
        let mut new_facing = it.facing.clone();
        for _ in 0..8 {
            let cand = facings[rng.below(4) as usize];
            if cand != it.facing {
                new_facing = cand.to_string();
                break;
            }
        }
        if new_facing == it.facing {
            return None;
        }
        let name = cat_name(&it.kind).to_string();
        let room = room_name(it.floor, it.x, it.y);
        let reason = self.reason_rotate(&it, &room);
        let who = self.who_main();
        let nows = fmt_ts(now);
        let mut applied = false;
        if let Some(pos) = self.state.furniture.iter().position(|f| f.id == it.id) {
            let f = &mut self.state.furniture[pos];
            f.facing = new_facing.clone();
            f.moved_at = nows.clone();
            applied = true;
        }
        if !applied {
            return None;
        }
        let text = format!(
            "{}把{}（{}）从（{},{}）转向{}：{}",
            who,
            name,
            room,
            it.x,
            it.y,
            facing_cn(&new_facing),
            reason
        );
        Some(Event {
            at: nows,
            kind: "furniture_rotate".into(),
            text,
            who,
            source: "home".into(),
            furniture_id: it.id.clone(),
            furniture: name,
            from: Some(Coord { floor: it.floor, x: it.x, y: it.y }),
            to: Some(Coord { floor: it.floor, x: it.x, y: it.y }),
            reason,
            new_item: false,
        })
    }

    fn next_item_num(&mut self) -> i64 {
        self.state.next_id += 1;
        self.state.next_id
    }

    // ---- 原因:必须由真实状态推出来 ----

    fn warehouse_total(&self) -> i64 {
        self.state.warehouse.values().sum()
    }

    fn top_crop(&self) -> Option<(String, i64)> {
        self.state
            .warehouse
            .iter()
            .max_by_key(|kv| *kv.1)
            .map(|(k, v)| (k.clone(), *v))
    }

    fn crowded_room(&self, floor: u8, x: i32, y: i32) -> bool {
        let room = match room_of(floor, x, y) {
            Some(r) => r.1,
            None => return false,
        };
        let cnt = self
            .state
            .furniture
            .iter()
            .filter(|f| match room_of(f.floor, f.x, f.y) {
                Some(r) => r.1 == room,
                None => false,
            })
            .count();
        cnt >= 5
    }

    fn last_event_hint(&self) -> String {
        for e in self.state.events.iter().rev() {
            if e.kind == "harvest" || e.kind == "plant" || e.kind == "task_done" {
                return e.text.clone();
            }
        }
        "手头的活".to_string()
    }

    fn weather_is(&self, kind: &str) -> bool {
        self.state.weather_kind.as_deref() == Some(kind)
    }

    fn reason_move(&self, item: &Item, to_room: &str) -> String {
        let name = cat_name(&item.kind);
        let hour = hour_of(now_ts());
        let wh = self.warehouse_total();
        if item.kind == "plant" && (self.weather_is("sunny") || hour < 11) {
            return format!("今天光线好,把{}挪到{}窗边,让它多晒会儿太阳。", name, to_room);
        }
        if wh >= 6 && (item.kind == "nstand" || item.kind == "shelf") {
            if let Some((crop, n)) = self.top_crop() {
                return format!(
                    "仓库里{}已经堆了 {} 份,把{}挪到{}墙边,收下来的东西顺手就能码。",
                    crop, n, name, to_room
                );
            }
        }
        if hour >= 20 && item.kind == "rug" {
            return format!("夜里要睡了,把{}挪到床边,下床踩着不凉。", name);
        }
        if self.crowded_room(item.floor, item.x, item.y) || self.crowded_room(item.floor, item.x + 1, item.y) {
            return format!("{}最近有点挤,把{}往里收了收,中间空出来好走路。", to_room, name);
        }
        if !self.state.last_staff_move.is_empty() && item.kind == "catbed" {
            return format!("家里人多起来了,把{}挪到{}角落,猫有地方躲。", name, to_room);
        }
        let hint = self.last_event_hint();
        format!("刚忙完「{}」,顺手把{}挪到{},不挡走路也顺手。", hint, name, to_room)
    }

    fn reason_add(&self, kind: &str, room: &str) -> String {
        let name = cat_name(kind);
        let hour = hour_of(now_ts());
        if self.warehouse_total() >= 6 {
            if let Some((crop, n)) = self.top_crop() {
                return format!(
                    "最近收的{}已经有 {} 份,仓库快放不下了,在{}添了{},东西有地方码。",
                    crop, n, room, name
                );
            }
        }
        if self.weather_is("sunny") {
            return format!("今天太阳好,{}空着一角,添了{},坐着晒太阳舒服。", room, name);
        }
        if hour >= 20 {
            return format!("晚上想在{}多待会儿,添了{},屋里亮堂一点。", room, name);
        }
        if self.crowded_room(1, 2, 2) {
            return format!("{}东西有点多,添了{},把角落利用起来,不乱。", room, name);
        }
        format!("{}空着一角,添了{},屋里看着更有生活气。", room, name)
    }

    fn reason_rotate(&self, item: &Item, room: &str) -> String {
        let name = cat_name(&item.kind);
        let hour = hour_of(now_ts());
        if hour >= 9 && hour < 17 && self.weather_is("sunny") {
            return format!("白天光从窗边过来,把{}转一下,正好迎着光。", name);
        }
        if hour >= 20 {
            return format!("晚上在{}待着,把{}转过来,坐着更顺。", room, name);
        }
        format!("在{}待久了想换个角度,把{}转一下,看着新鲜。", room, name)
    }

    // ============================================================
    // v2:七名角色 / 雇佣 / 寻路 / 工作 / 记忆同步
    // ============================================================

    fn default_layout() -> Value {
        let rooms = json!([
            {"floor":1,"key":"living","name":"客厅","x0":0,"y0":0,"x1":7,"y1":7},
            {"floor":1,"key":"kitchen","name":"厨房","x0":8,"y0":0,"x1":11,"y1":3},
            {"floor":1,"key":"hall","name":"玄关","x0":8,"y0":4,"x1":11,"y1":7},
            {"floor":2,"key":"bedroom","name":"卧室","x0":0,"y0":0,"x1":6,"y1":7},
            {"floor":2,"key":"study","name":"书房","x0":7,"y0":0,"x1":11,"y1":7}
        ]);
        let windows = json!([
            {"floor":1,"x0":2,"x1":4,"side":"top"},
            {"floor":1,"x0":9,"x1":10,"side":"top"},
            {"floor":2,"x0":2,"x1":4,"side":"top"},
            {"floor":2,"x0":0,"x1":1,"side":"left"}
        ]);
        let mut catalog = serde_json::Map::new();
        for e in CATALOG {
            let kind = e.0;
            if kind == "table" || kind == "dlight" {
                continue;
            }
            catalog.insert(
                kind.to_string(),
                json!({
                    "name": e.1, "w": e.2, "h": e.3,
                    "color": furn_color(kind),
                    "rooms": e.4,
                    "price": furn_price(kind),
                    "type": furn_info(kind).0,
                    "desc": furn_info(kind).1,
                    "usage": furn_info(kind).2
                }),
            );
        }
        json!({
            "grid": {"w": W, "h": H, "floors": 2, "cell_w": 56, "cell_h": 34},
            "rooms": rooms,
            "stair": {"x0": STAIR.0, "y0": STAIR.1, "x1": STAIR.2, "y1": STAIR.3},
            "door": {"floor": 1, "x": DOOR.0, "y": DOOR.1},
            "windows": windows,
            "catalog": catalog
        })
    }

    /// 这个角色此刻该在哪个场景:house / farm(初代 her_now 的作息)
    fn schedule_place(&self, i: usize, now: i64) -> String {
        let c = &self.state.characters[i];
        let phase = phase_of(now);
        let farm_jobs = ["water", "harvest", "plant", "weed"];
        if c.hired && farm_jobs.contains(&c.job.as_str()) {
            return "farm".to_string();
        }
        if c.id == "jingyu" {
            if matches!(phase, "浇水" | "除草" | "播种" | "发呆") {
                return "farm".to_string();
            }
            return "house".to_string();
        }
        "house".to_string()
    }

    fn her_text(&self, now: i64) -> String {
        let c = match self.state.characters.first() {
            Some(c) => c,
            None => return "在小家里".to_string(),
        };
        if c.place == "farm" {
            let job = c.action.as_ref().map(|a| a.job.clone()).unwrap_or_default();
            return match job.as_str() {
                "water" => "在田里给菜浇水".to_string(),
                "weed" => "蹲在田里拔草".to_string(),
                "plant" => "在田里撒种子".to_string(),
                "harvest" => "在田里摘熟的菜".to_string(),
                _ => "站在田边发呆".to_string(),
            };
        }
        let phase = phase_of(now);
        match phase {
            "吃饭" => "回屋吃饭去了".to_string(),
            "看电视" => "回屋了,窝在沙发上看电视".to_string(),
            "睡觉" => "回屋睡下了".to_string(),
            _ => match c.doing.as_str() {
                "read" => "在书房看书".to_string(),
                "work" => "在给绿植浇水".to_string(),
                _ => "在一楼窗边发呆".to_string(),
            },
        }
    }

    /// 在田里直接站到某块地上干活(初代田地页不做家具寻路)
    fn plan_farm(&mut self, i: usize, now: i64) {
        let phase = phase_of(now);
        let job0 = self.state.characters[i].job.clone();
        let want = if job0 == "water" || job0 == "harvest" || job0 == "plant" {
            job0
        } else {
            match phase {
                "浇水" => "water".to_string(),
                "除草" => "weed".to_string(),
                "播种" => "plant".to_string(),
                _ => "idle".to_string(),
            }
        };
        let mut pick: Option<(usize, String)> = None;
        for (idx, p) in self.state.plots.iter().enumerate() {
            if p.crop.is_none() {
                if want == "plant" && self.state.coins >= 3 && pick.is_none() {
                    pick = Some((idx, "plant".to_string()));
                }
                continue;
            }
            if want == "harvest" && self.mature(p, now) {
                pick = Some((idx, "harvest".to_string()));
                break;
            }
            if want == "water" && self.is_dry(p, now) {
                pick = Some((idx, "water".to_string()));
                break;
            }
            if want == "weed" && pick.is_none() {
                pick = Some((idx, "weed".to_string()));
            }
        }
        if pick.is_none() {
            // 没活可干:按固定横向路线在田里走 2~3 个角,别在同一列上下往返
            let from = self.char_cell(i);
            let blocked = self.occupied_by_chars(i, "farm");
            let routes: [[(i32, i32); 3]; 3] = [
                [(2, 3), (9, 3), (9, 6)],
                [(9, 3), (9, 6), (2, 6)],
                [(5, 2), (2, 5), (8, 6)],
            ];
            let ri = (i + (now / 15) as usize) % routes.len();
            let mut multi: Vec<Cell> = Vec::new();
            let mut cur = from.clone();
            for (rx, ry) in routes[ri].iter() {
                let dest = Cell { floor: from.floor, x: *rx, y: *ry };
                if !Self::farm_walkable(dest.floor, dest.x, dest.y) {
                    continue;
                }
                if blocked.contains(&(dest.floor, dest.x, dest.y)) {
                    continue;
                }
                if (dest.x - cur.x).abs() + (dest.y - cur.y).abs() < 2 {
                    continue;
                }
                if let Some(p) = self.find_path_farm(cur.clone(), dest.clone(), &blocked) {
                    if !p.is_empty() {
                        multi.extend(p);
                        cur = dest;
                    }
                }
            }
            if !multi.is_empty() {
                let c = &mut self.state.characters[i];
                c.place = "farm".to_string();
                c.action = Some(Action {
                    kind: "walk".to_string(),
                    job: "wander".to_string(),
                    target_kind: "cell".to_string(),
                    target_id: String::new(),
                    phase: "moving".to_string(),
                    started_at: fmt_ts(now),
                    until: String::new(),
                    note: "在田边走一圈".to_string(),
                });
                c.path = multi;
                c.path_started = fmt_ts(now);
                c.doing = "walk".to_string();
                c.task = "在田边走一圈".to_string();
                return;
            }
            let mut wander: Option<(Cell, Vec<Cell>)> = None;
            for _ in 0..24 {
                if let Some((x, y)) = self.random_free_cell(from.floor) {
                    let dest = Cell { floor: from.floor, x: x.floor() as i32, y: y.floor() as i32 };
                    if dest == from || !Self::farm_walkable(dest.floor, dest.x, dest.y) {
                        continue;
                    }
                    if blocked.contains(&(dest.floor, dest.x, dest.y)) {
                        continue;
                    }
                    if let Some(path) = self.find_path_farm(from.clone(), dest.clone(), &blocked) {
                        if !path.is_empty() {
                            wander = Some((dest, path));
                            break;
                        }
                    }
                }
            }
            if let Some((_dest, path)) = wander {
                let mut multi = path;
                let mut cur = self.char_cell(i);
                for _leg in 0..2 {
                    let mut leg: Option<(Cell, Vec<Cell>)> = None;
                    for _ in 0..24 {
                        if let Some((x, y)) = self.random_free_cell(cur.floor) {
                            let dest = Cell { floor: cur.floor, x: x.floor() as i32, y: y.floor() as i32 };
                            if dest == cur || !Self::farm_walkable(dest.floor, dest.x, dest.y) { continue; }
                            if blocked.contains(&(dest.floor, dest.x, dest.y)) { continue; }
                            if (dest.x - cur.x).abs() + (dest.y - cur.y).abs() < 2 { continue; }
                            if let Some(p) = self.find_path_farm(cur.clone(), dest.clone(), &blocked) {
                                if !p.is_empty() { leg = Some((dest, p)); break; }
                            }
                        }
                    }
                    match leg { Some((dest, p)) => { cur = dest; multi.extend(p); } None => break }
                }
                let c = &mut self.state.characters[i];
                c.place = "farm".to_string();
                c.action = Some(Action {
                    kind: "walk".to_string(),
                    job: "wander".to_string(),
                    target_kind: "cell".to_string(),
                    target_id: String::new(),
                    phase: "moving".to_string(),
                    started_at: fmt_ts(now),
                    until: String::new(),
                    note: "在田边走走".to_string(),
                });
                c.path = multi;
                c.path_started = fmt_ts(now);
                c.doing = "walk".to_string();
                c.task = "在田边走走".to_string();
                return;
            }
            let c = &mut self.state.characters[i];
            c.doing = "idle".to_string();
            c.action = None;
            c.task = "在田边站着".to_string();
            return;
        }
        let (idx, job) = pick.unwrap();
        let (pf, px, py) = {
            let p = &self.state.plots[idx];
            (p.floor, p.x, p.y)
        };
        let note = match job.as_str() {
            "water" => "去田里给菜浇水",
            "harvest" => "去田里摘熟的菜",
            "plant" => "去田里撒种子",
            "weed" => "去田里拔草",
            _ => "去田边站着",
        };
        // 刚切到田里:先站到田中央,再由寻路走过去
        if self.state.characters[i].place != "farm" {
            let c = &mut self.state.characters[i];
            c.place = "farm".to_string();
            c.floor = 1;
            c.x = 5.5;
            c.y = 4.5;
        }
        let from = self.char_cell(i);
        let dest = Cell { floor: pf, x: px, y: py };
        let blocked = self.occupied_by_chars(i, "farm");
        let path = self.find_path_farm(from.clone(), dest.clone(), &blocked).unwrap_or_default();
        let name = self.state.characters[i].name.clone();
        {
            let c = &mut self.state.characters[i];
            c.floor = pf;
            if path.is_empty() {
                c.x = px as f64 + 0.5;
                c.y = py as f64 + 0.5;
                c.action = Some(Action {
                    kind: "work".to_string(),
                    job: job.clone(),
                    target_kind: "plot".to_string(),
                    target_id: self.state.plots[idx].id.clone(),
                    phase: "working".to_string(),
                    started_at: fmt_ts(now),
                    until: fmt_ts(now + ACT_SECS),
                    note: note.to_string(),
                });
                c.path.clear();
                c.doing = anim_for_job(&job).to_string();
                c.task = note.to_string();
            } else {
                c.action = Some(Action {
                    kind: "walk".to_string(),
                    job: job.clone(),
                    target_kind: "plot".to_string(),
                    target_id: self.state.plots[idx].id.clone(),
                    phase: "moving".to_string(),
                    started_at: fmt_ts(now),
                    until: String::new(),
                    note: note.to_string(),
                });
                c.path = path;
                c.path_started = fmt_ts(now);
                c.doing = "walk".to_string();
                c.task = note.to_string();
            }
        }
        if self.state.characters[i]
            .action
            .as_ref()
            .map(|a| a.phase == "working")
            .unwrap_or(false)
        {
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "work_start".into(),
                text: format!("{}开始{}", name, note),
                who: name,
                source: "farm".into(),
                ..Default::default()
            });
            self.queue_character_state(i);
        }
    }

    fn ensure_roster(&mut self) {
        let mut old: Vec<Character> = std::mem::take(&mut self.state.characters);
        let mut out: Vec<Character> = Vec::new();
        for (i, (id, name, look, role, job)) in ROSTER.iter().enumerate() {
            let found = old
                .iter()
                .position(|c| c.id == *id || c.name == *name)
                .map(|p| old.remove(p));
            let mut c = match found {
                Some(mut c) => {
                    c.id = id.to_string();
                    c
                }
                None => Character {
                    id: id.to_string(),
                    name: name.to_string(),
                    energy: 80,
                    mood: 80,
                    full: 80,
                    clean: 80,
                    task: "空闲".into(),
                    role: role.to_string(),
                    look: look.to_string(),
                    avatar: None,
                    avatar_source: "pixel".into(),
                    floor: 1,
                    x: 2.5 + (i % 3) as f64,
                    y: 1.5,
                    doing: "idle".into(),
                    hired: i == 0,
                    hire_cost: if i == 0 { 0 } else { HIRE_COST },
                    job: job.to_string(),
                    action: None,
                    path: Vec::new(),
                    path_started: String::new(),
                    facing: "south".into(),
                    place: "house".into(),
                },
            };
            c.name = name.to_string();
            c.look = look.to_string();
            c.role = role.to_string();
            if c.job.is_empty() {
                c.job = job.to_string();
            }
            c.hired = if *id == "jingyu" { true } else { c.hired };
            c.hire_cost = if *id == "jingyu" { 0 } else { HIRE_COST };
            if c.place.is_empty() {
                c.place = "house".to_string();
            }
            if *id == "jingyu" && (c.x < 2.5 || c.y < 2.5) {
                c.x = 3.5;
                c.y = 3.5;
            }
            if c.floor != 1 && c.floor != 2 {
                c.floor = 1;
            }
            if c.x <= 0.0 || c.y <= 0.0 {
                c.x = 2.5 + (i % 3) as f64;
                c.y = 1.5;
            }
            if c.avatar_source.is_empty() {
                c.avatar_source = if c.avatar.is_some() { "album".into() } else { "pixel".into() };
            }
            if c.facing.is_empty() {
                c.facing = "south".into();
            }
            if !c.hired {
                c.action = None;
                c.path.clear();
                c.doing = "idle".into();
                c.task = "还没搬进来".into();
                c.floor = 1;
                c.x = 9.5;
                c.y = 7.5;
            }
            out.push(c);
        }
        self.state.characters = out;
        // 旧 staff 字段保留兼容,但不再参与工资/聘任
        self.state.staff.clear();
    }

    fn char_index(&self, id: &str) -> Option<usize> {
        self.state
            .characters
            .iter()
            .position(|c| c.id == id || c.name == id)
    }

    fn char_cell(&self, i: usize) -> Cell {
        let c = &self.state.characters[i];
        Cell {
            floor: if c.floor == 2 { 2 } else { 1 },
            x: c.x.floor() as i32,
            y: c.y.floor() as i32,
        }
    }

    fn furniture_footprint(&self, floor: u8) -> Vec<(i32, i32)> {
        let mut out = Vec::new();
        for it in &self.state.furniture {
            if it.floor != floor {
                continue;
            }
            let (_n, w, h, _r) = cat(&it.kind);
            for dy in 0..h {
                for dx in 0..w {
                    out.push((it.x + dx, it.y + dy));
                }
            }
        }
        out
    }

    fn walkable(&self, floor: u8, x: i32, y: i32) -> bool {
        if x < 0 || x >= W || y < 0 || y >= H {
            return false;
        }
        if room_of(floor, x, y).is_none() {
            return false;
        }
        let foot = self.furniture_footprint(floor);
        !foot.contains(&(x, y))
    }

    fn neighbors(c: &Cell) -> Vec<Cell> {
        let mut out = Vec::new();
        for (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
            let nx = c.x + dx;
            let ny = c.y + dy;
            if nx >= 0 && nx < W && ny >= 0 && ny < H {
                out.push(Cell { floor: c.floor, x: nx, y: ny });
            }
        }
        // 楼梯:两层同一格 (11,4..6)
        if c.x == STAIR.0 && c.y >= STAIR.1 && c.y <= STAIR.3 {
            out.push(Cell { floor: if c.floor == 1 { 2 } else { 1 }, x: c.x, y: c.y });
        }
        out
    }

    fn find_path(&self, from: Cell, to: Cell) -> Option<Vec<Cell>> {
        self.find_path_avoiding(from, to, &[])
    }

    fn farm_walkable(floor: u8, x: i32, y: i32) -> bool {
        FLOORS.contains(&floor) && x >= 0 && x < W && y >= 0 && y < H
    }

    fn find_path_farm(&self, from: Cell, to: Cell, blocked: &[(u8, i32, i32)]) -> Option<Vec<Cell>> {
        if from == to {
            return Some(Vec::new());
        }
        if !Self::farm_walkable(to.floor, to.x, to.y) || blocked.contains(&(to.floor, to.x, to.y)) {
            return None;
        }
        let mut q: VecDeque<Cell> = VecDeque::new();
        let mut seen: HashMap<Cell, Option<Cell>> = HashMap::new();
        q.push_back(from.clone());
        seen.insert(from.clone(), None);
        let mut guard = 0;
        while let Some(cur) = q.pop_front() {
            guard += 1;
            if guard > 900 {
                break;
            }
            if cur == to {
                let mut path = Vec::new();
                let mut node = cur;
                while let Some(prev) = seen.get(&node).cloned().flatten() {
                    path.push(node.clone());
                    node = prev;
                }
                path.reverse();
                return Some(path);
            }
            for nb in Self::neighbors(&cur) {
                if !Self::farm_walkable(nb.floor, nb.x, nb.y) || seen.contains_key(&nb) {
                    continue;
                }
                if blocked.contains(&(nb.floor, nb.x, nb.y)) {
                    continue;
                }
                seen.insert(nb.clone(), Some(cur.clone()));
                q.push_back(nb);
            }
        }
        None
    }

    fn adjacent_cells(&self, it: &Item) -> Vec<Cell> {
        let (_n, w, h, _r) = cat(&it.kind);
        let mut out = Vec::new();
        for dy in -1..=h {
            for dx in -1..=w {
                let inside_x = dx >= 0 && dx < w;
                let inside_y = dy >= 0 && dy < h;
                if inside_x && inside_y {
                    continue;
                }
                out.push(Cell { floor: it.floor, x: it.x + dx, y: it.y + dy });
            }
        }
        out
    }

    fn stand_spot(&self, it: &Item) -> Option<Cell> {
        self.adjacent_cells(it)
            .into_iter()
            .find(|c| self.walkable(c.floor, c.x, c.y))
    }

    /// 找最近且「真的走得到」的家具落点(避免家具被墙/其他家具堵死时原地卡住)
    fn nearest_of_kind(&self, kind: &str, from: &Cell, blocked: &[(u8, i32, i32)]) -> Option<(Cell, String)> {
        let mut best: Option<(usize, Cell, String)> = None;
        for it in &self.state.furniture {
            if it.kind != kind {
                continue;
            }
            for spot in self.adjacent_cells(it) {
                if !self.walkable(spot.floor, spot.x, spot.y) {
                    continue;
                }
                if blocked.contains(&(spot.floor, spot.x, spot.y)) {
                    continue;
                }
                if let Some(path) = self.find_path_avoiding(from.clone(), spot.clone(), blocked) {
                    // 顶排落点会让 2 格高的角色压到墙上,加一点距离惩罚,优先靠里
                    let d = path.len() + if spot.y < 2 { 100 } else { 0 };
                    if best.as_ref().map(|b| d < b.0).unwrap_or(true) {
                        best = Some((d, spot, it.id.clone()));
                    }
                }
            }
        }
        best.map(|b| (b.1, b.2))
    }

    fn char_cell_occupied(&self, except: usize, place: &str, floor: u8, x: i32, y: i32) -> bool {
        for (j, c) in self.state.characters.iter().enumerate() {
            if j == except || !c.hired || c.place != place {
                continue;
            }
            let cf = if c.floor == 2 { 2 } else { 1 };
            if cf == floor && c.x.floor() as i32 == x && c.y.floor() as i32 == y {
                return true;
            }
        }
        false
    }

    fn occupied_by_chars(&self, except: usize, place: &str) -> Vec<(u8, i32, i32)> {
        let mut out = Vec::new();
        for (j, c) in self.state.characters.iter().enumerate() {
            if j == except || !c.hired || c.place != place {
                continue;
            }
            let cf = if c.floor == 2 { 2 } else { 1 };
            out.push((cf, c.x.floor() as i32, c.y.floor() as i32));
        }
        out
    }

    fn find_path_avoiding(&self, from: Cell, to: Cell, blocked: &[(u8, i32, i32)]) -> Option<Vec<Cell>> {
        if from == to {
            return Some(Vec::new());
        }
        if !self.walkable(to.floor, to.x, to.y) {
            return None;
        }
        if blocked.contains(&(to.floor, to.x, to.y)) {
            return None;
        }
        let mut q: VecDeque<Cell> = VecDeque::new();
        let mut seen: HashMap<Cell, Option<Cell>> = HashMap::new();
        q.push_back(from.clone());
        seen.insert(from.clone(), None);
        let mut guard = 0;
        while let Some(cur) = q.pop_front() {
            guard += 1;
            if guard > 900 {
                break;
            }
            if cur == to {
                let mut path = Vec::new();
                let mut node = cur;
                while let Some(prev) = seen.get(&node).cloned().flatten() {
                    path.push(node.clone());
                    node = prev;
                }
                path.reverse();
                return Some(path);
            }
            for nb in Self::neighbors(&cur) {
                if !self.walkable(nb.floor, nb.x, nb.y) || seen.contains_key(&nb) {
                    continue;
                }
                if blocked.contains(&(nb.floor, nb.x, nb.y)) {
                    continue;
                }
                seen.insert(nb.clone(), Some(cur.clone()));
                q.push_back(nb);
            }
        }
        None
    }

    fn plot_stand(&self, p: &Plot) -> Option<Cell> {
        let c = Cell { floor: p.floor, x: p.x, y: p.y };
        if self.walkable(c.floor, c.x, c.y) {
            return Some(c);
        }
        for nb in Self::neighbors(&c) {
            if self.walkable(nb.floor, nb.x, nb.y) {
                return Some(nb);
            }
        }
        None
    }

    /// 地块落点:优先地块本身,其次相邻格,且必须「没被别人占、走得到」
    fn plot_stand_avoiding(&self, p: &Plot, blocked: &[(u8, i32, i32)], from: &Cell) -> Option<Cell> {
        let c = Cell { floor: p.floor, x: p.x, y: p.y };
        let mut cands = vec![c.clone()];
        cands.extend(Self::neighbors(&c));
        for cand in cands {
            if !self.walkable(cand.floor, cand.x, cand.y) {
                continue;
            }
            if blocked.contains(&(cand.floor, cand.x, cand.y)) {
                continue;
            }
            if self.find_path_avoiding(from.clone(), cand.clone(), blocked).is_some() {
                return Some(cand);
            }
        }
        None
    }

    fn free_cell_near(&self, floor: u8) -> (f64, f64) {
        let mut rng = Rng::new(seed_now());
        self.free_cell(floor, &mut rng).unwrap_or((2.5, 2.5))
    }

    // ---------------- 工作与生活安排 ----------------

    fn do_hire_char(&mut self, id: &str, now: i64) -> Result<Value, String> {
        let idx = self.char_index(id).ok_or_else(|| format!("没有这个角色:{}", id))?;
        {
            let c = &self.state.characters[idx];
            if c.id == "jingyu" {
                return Err("鲸鱼娘是初始角色,不需要雇佣".into());
            }
            if c.hired {
                return Err("这个角色已经雇佣过了".into());
            }
            if self.state.coins < HIRE_COST {
                return Err("金币不足 10,不能雇佣".into());
            }
            let hired_helpers = self
                .state
                .characters
                .iter()
                .filter(|c| c.hired && c.id != "jingyu")
                .count();
            if hired_helpers as i64 >= HIRE_SLOTS {
                return Err(format!("最多只能雇 {} 个人", HIRE_SLOTS));
            }
        }
        self.state.coins -= HIRE_COST;
        let mut cell = self.free_cell_near(1);
        for _ in 0..40 {
            let cx = cell.0.floor() as i32;
            let cy = cell.1.floor() as i32;
            if cx >= 2 && cx <= 9 && cy >= 2
                && !self.char_cell_occupied(idx, "house", 1, cx, cy)
            {
                break;
            }
            cell = self.free_cell_near(1);
        }
        let (x, y) = cell;
        {
            let c = &mut self.state.characters[idx];
            c.hired = true;
            c.hire_cost = HIRE_COST;
            c.floor = 1;
            c.x = x;
            c.y = y;
            c.doing = "idle".into();
            c.task = "刚搬进来".into();
            c.action = None;
            c.path.clear();
        }
        let name = self.state.characters[idx].name.clone();
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "hire", "amount": -HIRE_COST,
            "note": format!("雇佣 {}（一次性雇佣费）", name), "balance": self.state.coins
        }));
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "hire".into(),
            text: format!("鲸鱼娘花 {} 金币雇佣了 {},从今天起一起生活。", HIRE_COST, name),
            who: "鲸鱼娘".into(),
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "character": self.state.characters[idx], "cost": HIRE_COST }))
    }

    fn do_fire_char(&mut self, id: &str, now: i64) -> Result<Value, String> {
        let idx = self.char_index(id).ok_or_else(|| format!("没有这个角色:{}", id))?;
        if self.state.characters[idx].id == "jingyu" {
            return Err("鲸鱼娘是初始角色,不能解雇".into());
        }
        if !self.state.characters[idx].hired {
            return Err("这个角色还没被雇佣".into());
        }
        let name = self.state.characters[idx].name.clone();
        {
            let c = &mut self.state.characters[idx];
            c.hired = false;
            c.action = None;
            c.path.clear();
            c.doing = "idle".into();
            c.task = "还没搬进来".into();
            c.floor = 1;
            c.x = 9.5;
            c.y = 7.5;
        }
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "fire".into(),
            text: format!("{}和 {} 道别,雇佣关系结束了。", self.who_main(), name),
            who: self.who_main(),
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "character": self.state.characters[idx] }))
    }

    fn do_assign_work(&mut self, id: &str, job: &str, now: i64) -> Result<Value, String> {
        let idx = self.char_index(id).ok_or_else(|| format!("没有这个角色:{}", id))?;
        if !self.state.characters[idx].hired {
            return Err("还没雇佣这个角色,不能安排工作".into());
        }
        let allowed = ["water", "harvest", "plant", "tidy", "read", "rest", ""];
        if !allowed.contains(&job) {
            return Err(format!("不认识这个工作:{}", job));
        }
        let name = self.state.characters[idx].name.clone();
        {
            let c = &mut self.state.characters[idx];
            c.job = job.to_string();
            c.action = None;
            c.path.clear();
            c.doing = "idle".into();
            c.task = if job.is_empty() { "空闲".into() } else { format!("待命:{}", job_cn(job)) };
        }
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "assign_work".into(),
            text: format!("{}给 {} 安排了工作:{}", self.who_main(), name, job_cn(job)),
            who: self.who_main(),
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "character": self.state.characters[idx], "job": job }))
    }

    fn do_cancel_work(&mut self, id: &str) -> Result<Value, String> {
        let idx = self.char_index(id).ok_or_else(|| format!("没有这个角色:{}", id))?;
        let c = &mut self.state.characters[idx];
        c.job.clear();
        c.action = None;
        c.path.clear();
        c.doing = "idle".into();
        c.task = "空闲".into();
        Ok(json!({ "id": id }))
    }

    fn random_free_cell(&self, floor: u8) -> Option<(f64, f64)> {
        let mut rng = Rng::new(seed_now());
        self.free_cell(floor, &mut rng)
    }

    // ---------------- 每帧推进:移动 -> 到达 -> 干活 ----------------

    fn plan_characters(&mut self, now: i64) {
        let n = self.state.characters.len();
        // 第一遍:按作息/工作算想去的场景;鲸鱼娘先占位
        let mut desired: Vec<String> = Vec::with_capacity(n);
        let mut home_n = 0i32;
        let mut farm_n = 0i32;
        for i in 0..n {
            if !self.state.characters[i].hired {
                desired.push("house".to_string());
                continue;
            }
            let want = self.schedule_place(i, now);
            if self.state.characters[i].id == "jingyu" {
                if want == "farm" {
                    farm_n += 1;
                } else {
                    home_n += 1;
                }
            }
            desired.push(want);
        }
        // 第二遍:雇工按名额调剂,每个场景最多 3 人
        for i in 0..n {
            if !self.state.characters[i].hired || self.state.characters[i].id == "jingyu" {
                continue;
            }
            let mut want = desired[i].clone();
            if want == "house" && home_n >= 3 {
                want = "farm".to_string();
            } else if want == "farm" && farm_n >= 3 {
                want = "house".to_string();
            }
            if want == "house" {
                home_n += 1;
            } else {
                farm_n += 1;
            }
            desired[i] = want;
        }

        let mut reserved: Vec<(u8, i32, i32)> = Vec::new();
        for i in 0..n {
            if !self.state.characters[i].hired {
                continue;
            }
            let want_place = desired[i].clone();
            let cur_place = self.state.characters[i].place.clone();
            if want_place != cur_place {
                let (sx, sy) = if want_place == "farm" { (5.5, 4.5) } else { (3.5, 3.5) };
                let c = &mut self.state.characters[i];
                c.place = want_place.clone();
                c.floor = 1;
                c.x = sx;
                c.y = sy;
                c.action = None;
                c.path.clear();
                c.doing = "idle".to_string();
                c.task = "".to_string();
            }
            let busy = self.state.characters[i]
                .action
                .as_ref()
                .map(|a| a.phase == "moving" || a.phase == "working")
                .unwrap_or(false);
            if busy {
                if let Some(last) = self.state.characters[i].path.last() {
                    reserved.push((last.floor, last.x, last.y));
                }
                continue;
            }
            if self.state.characters[i].place == "farm" {
                self.plan_farm(i, now);
                continue;
            }
            let mut blocked = self.occupied_by_chars(i, "house");
            blocked.extend(reserved.iter().cloned());
            if self.plan_one(i, now, &blocked) {
                if let Some(last) = self.state.characters[i].path.last() {
                    reserved.push((last.floor, last.x, last.y));
                }
            }
        }
    }

    fn plan_one(&mut self, i: usize, now: i64, blocked: &[(u8, i32, i32)]) -> bool {
        let from = self.char_cell(i);
        let hour = hour_of(now);
        let job = self.state.characters[i].job.clone();
        let name = self.state.characters[i].name.clone();
        let mut goal: Option<(Cell, String, String, String, String, String)> = None;

        if !job.is_empty() {
            match job.as_str() {
                "water" => {
                    let mut done = false;
                    for p in &self.state.plots {
                        if p.crop.is_some() && p.watered_at.is_none() {
                            let cname = crop_name(p.crop.as_deref().unwrap_or(""));
                            goal = Some((
                                match self.plot_stand_avoiding(p, blocked, &from) { Some(c) => c, _ => continue },
                                "water".into(),
                                "plot".into(),
                                p.id.clone(),
                                format!("去给{}浇水", cname),
                                "work".into(),
                            ));
                            done = true;
                            break;
                        }
                    }
                    if !done {
                        if let Some((spot, id)) = self.nearest_of_kind("plant", &from, blocked) {
                            goal = Some((spot, "water".into(), "furniture".into(), id, "去给绿植浇水".into(), "work".into()));
                        }
                    }
                }
                "harvest" => {
                    for p in &self.state.plots {
                        if self.mature(p, now) {
                            goal = Some((
                                match self.plot_stand_avoiding(p, blocked, &from) { Some(c) => c, _ => continue },
                                "harvest".into(),
                                "plot".into(),
                                p.id.clone(),
                                format!("去收{}", crop_name(p.crop.as_deref().unwrap_or(""))),
                                "work".into(),
                            ));
                            break;
                        }
                    }
                }
                "plant" => {
                    if self.state.coins >= 3 {
                        if let Some(p) = self.state.plots.iter().find(|p| p.crop.is_none()) {
                            goal = Some((
                                match self.plot_stand_avoiding(p, blocked, &from) { Some(c) => c, _ => return false },
                                "plant".into(),
                                "plot".into(),
                                p.id.clone(),
                                "去大田播种".into(),
                                "work".into(),
                            ));
                        }
                    }
                }
                "tidy" => {
                    if let Some(it) = self.state.furniture.iter().find(|f| is_movable(&f.kind) && !f.locked) {
                        if let Some(spot) = self.stand_spot(it) {
                            if !blocked.contains(&(spot.floor, spot.x, spot.y)) {
                                goal = Some((spot, "tidy".into(), "furniture".into(), it.id.clone(), format!("去整理{}", cat_name(&it.kind)), "work".into()));
                            }
                        }
                    }
                }
                "read" => {
                    let mk = ["desk", "shelf", "chair"];
                    for k in mk {
                        if let Some((spot, id)) = self.nearest_of_kind(k, &from, blocked) {
                            goal = Some((spot, "read".into(), "furniture".into(), id, "去看书".into(), "read".into()));
                            break;
                        }
                    }
                }
                _ => {}
            }
        }

        if goal.is_none() {
            let key = Self::day_key((hour + (i as u32) * 2) % 24);
            let fk = SCHEDULE_FURN.iter().find(|x| x.0 == key).map(|x| x.1).unwrap_or("");
            if !fk.is_empty() {
                if let Some((spot, id)) = self.nearest_of_kind(fk, &from, blocked) {
                    let note = format!("去{}", schedule_note(key));
                    goal = Some((spot, key.to_string(), "furniture".into(), id, note, schedule_anim(key).into()));
                }
            }
        }

        let (dest, a_job, tk, tid, note, anim) = match goal {
            Some(g) => g,
            None => {
                let mut wander: Option<Cell> = None;
                for _ in 0..12 {
                    if let Some((x, y)) = self.random_free_cell(from.floor) {
                        let c = Cell { floor: from.floor, x: x.floor() as i32, y: y.floor() as i32 };
                        if !blocked.contains(&(c.floor, c.x, c.y)) {
                            wander = Some(c);
                            break;
                        }
                    }
                }
                match wander {
                    Some(c) => (c, "wander".into(), "cell".into(), String::new(), "随便走走".into(), "walk".into()),
                    None => return false,
                }
            }
        };
        if !self.walkable(dest.floor, dest.x, dest.y) || blocked.contains(&(dest.floor, dest.x, dest.y)) {
            return false;
        }
        let mut path = self.find_path_avoiding(from.clone(), dest.clone(), blocked).unwrap_or_default();
        if path.is_empty() && from != dest {
            return false;
        }
        if path.is_empty() {
            // 已经站在干活点:走一段 2~3 段的散步再回来,避免原地 repeating / 一条线往返
            let mut multi: Vec<Cell> = Vec::new();
            let mut cur = from.clone();
            for _leg in 0..3 {
                let mut leg: Option<(Cell, Vec<Cell>)> = None;
                for _ in 0..30 {
                    if let Some((x, y)) = self.random_free_cell(cur.floor) {
                        let wander = Cell { floor: cur.floor, x: x.floor() as i32, y: y.floor() as i32 };
                        if wander == cur || !self.walkable(wander.floor, wander.x, wander.y) {
                            continue;
                        }
                        if blocked.contains(&(wander.floor, wander.x, wander.y)) {
                            continue;
                        }
                        if (wander.x - cur.x).abs() + (wander.y - cur.y).abs() < 2 {
                            continue;
                        }
                        if let Some(p) = self.find_path_avoiding(cur.clone(), wander.clone(), blocked) {
                            if !p.is_empty() {
                                leg = Some((wander, p));
                                break;
                            }
                        }
                    }
                }
                match leg {
                    Some((dest, p)) => { cur = dest; multi.extend(p); }
                    None => break,
                }
            }
            if !multi.is_empty() {
                let c = &mut self.state.characters[i];
                c.action = Some(Action {
                    kind: "walk".into(),
                    job: "wander".into(),
                    target_kind: "cell".into(),
                    target_id: String::new(),
                    phase: "moving".into(),
                    started_at: fmt_ts(now),
                    until: String::new(),
                    note: "随便走走".into(),
                });
                c.path = multi;
                c.path_started = fmt_ts(now);
                c.doing = "walk".into();
                c.task = "随便走走".into();
                return true;
            }
        }
        {
            let c = &mut self.state.characters[i];
            c.action = Some(Action {
                kind: "walk".into(),
                job: a_job,
                target_kind: tk,
                target_id: tid,
                phase: "moving".into(),
                started_at: fmt_ts(now),
                until: String::new(),
                note: note.clone(),
            });
            c.path = path;
            c.path_started = fmt_ts(now);
            c.doing = "walk".into();
            c.task = note;
        }
        self.arrive_if_here(i, now);
        true
    }

    fn arrive_if_here(&mut self, i: usize, now: i64) {
        if self.state.characters[i].path.is_empty() {
            let moving = self.state.characters[i]
                .action
                .as_ref()
                .map(|a| a.phase == "moving")
                .unwrap_or(false);
            if moving {
                self.arrive(i, now);
            }
        }
    }

    fn advance_motion(&mut self, now: i64) {
        for i in 0..self.state.characters.len() {
            let moving = self.state.characters[i]
                .action
                .as_ref()
                .map(|a| a.phase == "moving")
                .unwrap_or(false);
            if !moving {
                continue;
            }
            let started = parse_ts(&self.state.characters[i].path_started).unwrap_or(now);
            let steps = (((now - started).max(0) as f64) * MOVE_SPEED).floor() as usize;
            if steps == 0 {
                continue;
            }
            let take = steps.min(self.state.characters[i].path.len());
            let mut taken = 0;
            let mut blocked = false;
            for k in 0..take {
                let cell = self.state.characters[i].path[k].clone();
                if self.char_cell_occupied(i, &self.state.characters[i].place.clone(), cell.floor, cell.x, cell.y) {
                    blocked = true;
                    break;
                }
                taken = k + 1;
            }
            let take = taken;
            if take > 0 {
                let last = self.state.characters[i].path[take - 1].clone();
                self.state.characters[i].path.drain(0..take);
                let c = &mut self.state.characters[i];
                c.floor = last.floor;
                c.x = last.x as f64 + 0.5;
                c.y = last.y as f64 + 0.5;
                c.path_started = fmt_ts(now);
            }
            if blocked {
                // 路被另一个角色挡住:放弃当前路径,下一 tick 让 plan 重新避让,
                // 否则两人互挡会永久卡在 path 上(田地抽搐/原地卡死)。
                let c = &mut self.state.characters[i];
                c.action = None;
                c.path.clear();
                c.path_started = fmt_ts(now);
                c.doing = "idle".to_string();
                c.task = String::new();
                continue;
            }
            if self.state.characters[i].path.is_empty() {
                self.arrive(i, now);
            }
        }
    }

    fn arrive(&mut self, i: usize, now: i64) {
        let action = match self.state.characters[i].action.clone() {
            Some(a) => a,
            None => return,
        };
        let duration = match action.job.as_str() {
            "sleep" => ACT_SECS * 10,
            "read" => ACT_SECS * 4,
            "eat" => ACT_SECS * 2,
            "tv" | "stare" => ACT_SECS * 3,
            _ => ACT_SECS,
        };
        let name = self.state.characters[i].name.clone();
        let note = action.note.clone();
        {
            let c = &mut self.state.characters[i];
            if let Some(a) = c.action.as_mut() {
                a.phase = "working".into();
                a.started_at = fmt_ts(now);
                a.until = fmt_ts(now + duration);
            }
            c.doing = anim_for_job(&action.job).to_string();
            c.task = note.clone();
        }
        let text = format!("{}开始{}", name, note);
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "work_start".into(),
            text,
            who: name,
            source: "home".into(),
            ..Default::default()
        });
        self.queue_character_state(i);
    }

    fn finish_actions(&mut self, now: i64) {
        for i in 0..self.state.characters.len() {
            let done = self.state.characters[i]
                .action
                .as_ref()
                .filter(|a| a.phase == "working")
                .and_then(|a| parse_ts(&a.until))
                .map(|until| now >= until)
                .unwrap_or(false);
            if done {
                self.complete_action(i, now);
            }
        }
    }

    fn complete_action(&mut self, i: usize, now: i64) {
        let action = match self.state.characters[i].action.clone() {
            Some(a) => a,
            None => return,
        };
        let name = self.state.characters[i].name.clone();
        let mut result = String::from("完成");
        match action.job.as_str() {
            "water" => {
                if action.target_kind == "plot" {
                    if let Some(p) = self.state.plots.iter_mut().find(|p| p.id == action.target_id) {
                        p.watered_at = Some(fmt_ts(now));
                        let cname = crop_name(p.crop.as_deref().unwrap_or(""));
                        result = format!("给{}浇了水", cname);
                    }
                } else if let Some(it) = self.state.furniture.iter_mut().find(|f| f.id == action.target_id) {
                    it.used_count += 1;
                    it.last_used_at = fmt_ts(now);
                    result = format!("给{}浇了水", cat_name(&it.kind));
                }
            }
            "harvest" => {
                if let Some(p) = self.state.plots.iter_mut().find(|p| p.id == action.target_id) {
                    let crop = p.crop.clone().unwrap_or_default();
                    if p.crop.is_some() {
                        let cname = crop_name(&crop);
                        p.crop = None;
                        p.planted_at = None;
                        p.watered_at = None;
                        p.fertilized_at = None;
                        p.harvests += 1;
                        let n = self.state.warehouse.get(&cname).copied().unwrap_or(0) + 1;
                        self.state.warehouse.insert(cname.clone(), n);
                        result = format!("收了 1 份{},放进仓库", cname);
                    }
                }
            }
            "plant" => {
                if self.state.coins >= 3 {
                    if let Some(p) = self.state.plots.iter_mut().find(|p| p.id == action.target_id) {
                        if p.crop.is_none() {
                            let mut rng = Rng::new(seed_now());
                            let crop = CROPS[rng.below(CROPS.len() as u64) as usize].0.to_string();
                            self.state.coins -= 3;
                            p.crop = Some(crop.clone());
                            p.planted_at = Some(fmt_ts(now));
                            p.watered_at = None;
                            p.fertilized_at = None;
                            result = format!("种下了{}", crop_name(&crop));
                        }
                    }
                }
            }
            "weed" => {
                result = "把田里的草拔了".to_string();
            }
            "tidy" => {
                if let Some(it) = self.state.furniture.iter_mut().find(|f| f.id == action.target_id) {
                    it.used_count += 1;
                    it.last_used_at = fmt_ts(now);
                    result = format!("把{}整理了一下", cat_name(&it.kind));
                }
            }
            "read" | "sleep" | "eat" | "tv" | "stare" => {
                if action.target_kind == "furniture" {
                    if let Some(it) = self.state.furniture.iter_mut().find(|f| f.id == action.target_id) {
                        it.used_count += 1;
                        it.last_used_at = fmt_ts(now);
                    }
                }
                let energy_delta = match action.job.as_str() {
                    "sleep" => 12,
                    "eat" => 8,
                    "read" => -1,
                    _ => 1,
                };
                let c = &mut self.state.characters[i];
                c.energy = (c.energy + energy_delta).clamp(0, 100);
                result = format!("{}完了,体力 {} ", job_cn(&action.job), c.energy);
            }
            _ => {}
        }

        // 写生活日记(工作/休息/家具使用)
        let text = format!("{}{}", name, result);
        let kind = if action.job == "water" || action.job == "harvest" || action.job == "plant" {
            "work_done"
        } else if action.job == "tidy" {
            "furniture_use"
        } else if action.job == "sleep" {
            "sleep"
        } else {
            "rest"
        };
        self.push_event(Event {
            at: fmt_ts(now),
            kind: kind.into(),
            text: text.clone(),
            who: name.clone(),
            source: if kind == "work_done" { "farm".into() } else { "home".into() },
            furniture_id: if action.target_kind == "furniture" { action.target_id.clone() } else { String::new() },
            ..Default::default()
        });
        let dedupe = format!(
            "{}|{}|{}|{}",
            self.state.characters[i].id, action.job, action.target_id, fmt_ts(now)
        );
        let dup = self
            .state
            .work_history
            .iter()
            .take(30)
            .any(|h| h.get("dedupe").and_then(|v| v.as_str()) == Some(dedupe.as_str()));
        if !dup {
            self.state.work_history.insert(
                0,
                json!({
                    "at": fmt_ts(now), "character_id": self.state.characters[i].id,
                    "name": name, "job": action.job, "target": action.target_id,
                    "result": result, "note": action.note, "dedupe": dedupe
                }),
            );
            self.state.work_history.truncate(200);
        }
        {
            let c = &mut self.state.characters[i];
            c.action = None;
            c.doing = "idle".into();
            c.task = "空闲".into();
        }
        self.queue_character_state(i);
    }

    // ---------------- 统一记忆库同步 ----------------

    fn life_state_text(&self, c: &Character) -> String {
        let doing = match c.doing.as_str() {
            "walk" => "正在走动".to_string(),
            "work" => {
                if let Some(a) = &c.action {
                    format!("正在{}", job_cn(&a.job))
                } else {
                    "正在干活".to_string()
                }
            }
            "read" => "正在看书".to_string(),
            "sleep" => "正在睡觉".to_string(),
            "eat" => "正在吃饭".to_string(),
            "sit" => "正在休息".to_string(),
            "rest" => "正在休息".to_string(),
            _ => "正在发呆".to_string(),
        };
        let room = room_name(c.floor, c.x.floor() as i32, c.y.floor() as i32);
        format!("{}在{}{}", c.name, room, doing)
    }

    fn queue_character_state(&mut self, i: usize) {
        if i >= self.state.characters.len() {
            return;
        }
        let c = &self.state.characters[i];
        let content = self.life_state_text(c);
        let state_ref = format!("home:state:{}", c.id);
        let nows = fmt_ts(now_ts());
        let context = json!({
            "type": "character_state",
            "engine": "home",
            "character_id": c.id,
            "name": c.name,
            "doing": c.doing,
            "task": c.task,
            "floor": c.floor,
            "x": c.x,
            "y": c.y,
            "room": room_name(c.floor, c.x.floor() as i32, c.y.floor() as i32),
            "updated_at": nows
        });
        let body = json!({
            "content": content, "source": "home", "user_id": "local", "kind": "fact",
            "occurred_at": nows, "permission": "shared",
            "source_ref": state_ref, "context": context
        });
        let method = if self.state.memory_ids.contains_key(&state_ref) { "PATCH" } else { "POST" };
        let path = if method == "PATCH" {
            format!("/api/memory/{}", self.state.memory_ids.get(&state_ref).cloned().unwrap_or_default())
        } else {
            "/api/memory".to_string()
        };
        // 同一角色的旧状态同步请求先清掉,只保留最新
        self.state.memory_queue.retain(|q| {
            q.get("state_ref").and_then(|v| v.as_str()) != Some(state_ref.as_str())
        });
        self.state.memory_queue.push(json!({
            "method": method, "path": path, "body": body,
            "state_ref": state_ref, "attempts": 0
        }));
        if self.state.memory_queue.len() > 500 {
            let cut = self.state.memory_queue.len() - 500;
            self.state.memory_queue.drain(0..cut);
        }
    }

    fn queue_life_event(&mut self, ev: &Event) {
        if !memory_syncable(&ev.kind) {
            return;
        }
        // 同一角色同一类事件 10 分钟内最多写 1 条,避免记忆树被流水账刷满
        let gate_key = format!("{}:{}", ev.who, ev.kind);
        let now_i = now_ts();
        if let Some(last) = self.state.memory_gate.get(&gate_key).copied() {
            if now_i - last < 600 {
                return;
            }
        }
        self.state.memory_gate.insert(gate_key, now_i);
        self.state.event_seq += 1;
        let seq = self.state.event_seq;
        let nows = fmt_ts(now_ts());
        let source = if ev.source == "farm" { "farm" } else { "home" };
        let body = json!({
            "content": ev.text,
            "source": source,
            "user_id": "local",
            "kind": "fact",
            "occurred_at": if ev.at.is_empty() { nows.clone() } else { ev.at.clone() },
            "permission": "shared",
            "source_ref": format!("home:event:{}", seq),
            "context": {
                "type": "life_event",
                "engine": "home",
                "event": ev.kind,
                "who": ev.who,
                "furniture_id": ev.furniture_id,
                "room": "",
                "category": event_category(&ev.kind),
                "synced_at": nows
            }
        });
        self.state.memory_queue.push(json!({
            "method": "POST", "path": "/api/memory", "body": body,
            "state_ref": "", "attempts": 0
        }));
        if self.state.memory_queue.len() > 500 {
            let cut = self.state.memory_queue.len() - 500;
            self.state.memory_queue.drain(0..cut);
        }
    }

    fn sync_memory(&mut self) {
        // 单测不连真实统一记忆库(避免污染开发库);线上由 exe 后台线程调用
        if cfg!(test) {
            return;
        }
        if self.state.memory_queue.is_empty() {
            return;
        }
        let base = std::env::var("RIVULET_MEMORY")
            .unwrap_or_else(|_| "http://127.0.0.1:18091".to_string());
        let timeout = Duration::from_millis(1500);
        let mut done = 0;
        while done < 30 && !self.state.memory_queue.is_empty() {
            let item = self.state.memory_queue[0].clone();
            let method = item.get("method").and_then(|v| v.as_str()).unwrap_or("POST").to_string();
            let path = item.get("path").and_then(|v| v.as_str()).unwrap_or("/api/memory").to_string();
            let body = item.get("body").cloned().unwrap_or(json!({}));
            let state_ref = item.get("state_ref").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let url = format!("{}{}", base.trim_end_matches('/'), path);
            let req = if method == "PATCH" {
                ureq::request("PATCH", &url)
            } else {
                ureq::post(&url)
            };
            let res = req
                .timeout(timeout)
                .set("Content-Type", "application/json; charset=utf-8")
                .send_string(&body.to_string());
            match res {
                Ok(resp) => {
                    let data: Value = resp.into_json().unwrap_or(json!({}));
                    if !state_ref.is_empty() {
                        if let Some(id) = data.get("id").and_then(|v| v.as_str()) {
                            self.state.memory_ids.insert(state_ref.clone(), id.to_string());
                        }
                    }
                    self.state.memory_queue.remove(0);
                    self.state.memory_last_ok = fmt_ts(now_ts());
                    done += 1;
                }
                Err(ureq::Error::Status(code, resp)) => {
                    let detail = resp.into_string().unwrap_or_default();
                    if code == 404 && method == "PATCH" {
                        self.state.memory_ids.remove(&state_ref);
                        if let Some(m) = self.state.memory_queue[0].as_object_mut() {
                            m.insert("method".to_string(), json!("POST"));
                            m.insert("path".to_string(), json!("/api/memory"));
                        }
                        continue;
                    }
                    self.state.memory_last_err = format!("HTTP {} {}", code, detail.chars().take(120).collect::<String>());
                    break;
                }
                Err(e) => {
                    self.state.memory_last_err = format!("记忆服务未启动/不可达: {}", e);
                    break;
                }
            }
        }
        if self.state.memory_queue.is_empty() {
            self.state.memory_last_err.clear();
        }
    }

    // ---------------- 快照 / 状态 ----------------

    pub fn snapshot(&mut self) -> Value {
        self.maybe_tick();
        let now = now_ts();
        let phase = phase_of(now);

        let furniture: Vec<Value> = self
            .state
            .furniture
            .iter()
            .map(|f| {
                let (name, w, h, _rooms) = cat(&f.kind);
                json!({
                    "id": &f.id,
                    "kind": &f.kind,
                    "name": name,
                    "floor": f.floor,
                    "x": f.x,
                    "y": f.y,
                    "w": w,
                    "h": h,
                    "facing": &f.facing,
                    "locked": f.locked,
                    "movable": is_movable(&f.kind),
                    "room": room_name(f.floor, f.x, f.y),
                    "added_at": &f.added_at,
                    "moved_at": &f.moved_at,
                    "type": furn_info(&f.kind).0,
                    "desc": furn_info(&f.kind).1,
                    "usage": furn_info(&f.kind).2,
                    "used_count": f.used_count,
                    "last_used_at": &f.last_used_at
                })
            })
            .collect();

        let plots: Vec<Value> = self
            .state
            .plots
            .iter()
            .map(|p| {
                let mature = self.mature(p, now);
                let prog = self.progress(p, now);
                json!({
                    "id": &p.id,
                    "floor": p.floor,
                    "x": p.x,
                    "y": p.y,
                    "crop": &p.crop,
                    "crop_name": p.crop.as_deref().map(crop_name).unwrap_or_default(),
                    "planted_at": &p.planted_at,
                    "watered_at": &p.watered_at,
                    "fertilized_at": &p.fertilized_at,
                    "harvests": p.harvests,
                    "mature": mature,
                    "progress": (prog * 1000.0).round() / 1000.0,
                    "dry": self.is_dry(p, now),
                    "stage": self.crop_stage(p, now),
                    "left_minutes": self.left_minutes(p, now)
                })
            })
            .collect();

        let chars: Vec<Value> = self
            .state
            .characters
            .iter()
            .map(|c| {
                json!({
                    "id": &c.id,
                    "name": &c.name,
                    "energy": c.energy,
                    "mood": c.mood,
                    "full": c.full,
                    "clean": c.clean,
                    "task": &c.task,
                    "role": &c.role,
                    "look": &c.look,
                    "avatar": &c.avatar,
                    "avatar_source": &c.avatar_source,
                    "floor": c.floor,
                    "x": c.x,
                    "y": c.y,
                    "doing": &c.doing,
                    "facing": &c.facing,
                    "place": &c.place,
                    "hired": c.hired,
                    "hire_cost": c.hire_cost,
                    "job": &c.job,
                    "action": &c.action,
                    "moving": c.action.as_ref().map(|a| a.phase == "moving").unwrap_or(false),
                    "path": &c.path,
                    "path_started": &c.path_started
                })
            })
            .collect();

        let pos = self
            .state
            .characters
            .iter()
            .find(|c| c.hired)
            .or_else(|| self.state.characters.first())
            .map(|c| {
                json!({
                    "floor": c.floor, "x": c.x, "y": c.y, "doing": c.doing, "t": self.state.last_tick
                })
            });

        let staff: Vec<Value> = self
            .state
            .characters
            .iter()
            .filter(|c| c.hired && c.id != "jingyu")
            .map(|s| {
                json!({
                    "id": &s.id, "name": &s.name, "role": &s.role, "wage": 0,
                    "look": &s.look, "avatar": &s.avatar, "avatar_source": &s.avatar_source,
                    "floor": s.floor, "x": s.x, "y": s.y,
                    "task": &s.task, "job": &s.job, "hired_at": ""
                })
            })
            .collect();

        let next_adjust = parse_ts(&self.state.last_adjust_at)
            .map(|t| fmt_ts(t + ADJUST_SECS))
            .unwrap_or_default();

        let available: Vec<Value> = self
            .state
            .characters
            .iter()
            .filter(|c| !c.hired)
            .map(|c| json!({
                "id": &c.id, "name": &c.name, "look": &c.look, "role": &c.role,
                "hire_cost": c.hire_cost, "avatar": &c.avatar
            }))
            .collect();
        let hired_list: Vec<Value> = self
            .state
            .characters
            .iter()
            .filter(|c| c.hired)
            .map(|c| json!({
                "id": &c.id, "name": &c.name, "look": &c.look, "role": &c.role,
                "job": &c.job, "task": &c.task, "doing": &c.doing,
                "energy": c.energy, "floor": c.floor, "x": c.x, "y": c.y,
                "avatar": &c.avatar, "hire_cost": c.hire_cost
            }))
            .collect();
        let diary: Vec<Value> = self
            .state
            .events
            .iter()
            .rev()
            .take(80)
            .map(|e| json!({
                "at": &e.at, "type": &e.kind, "category": event_category(&e.kind),
                "source": &e.source,
                "text": &e.text, "who": &e.who,
                "furniture_id": &e.furniture_id, "furniture": &e.furniture,
                "from": &e.from, "to": &e.to, "reason": &e.reason
            }))
            .collect();
        let her_char = self.state.characters.first().cloned();
        let her_place = her_char.as_ref().map(|c| c.place.clone()).unwrap_or_else(|| "house".into());
        let her_doing = her_char.as_ref().map(|c| c.doing.clone()).unwrap_or_default();
        let her_floor = her_char.as_ref().map(|c| c.floor).unwrap_or(1);
        let her_text = self.her_text(now);

        let memory_status = json!({
            "queue": self.state.memory_queue.len(),
            "last_ok": &self.state.memory_last_ok,
            "last_err": &self.state.memory_last_err,
            "reachable": self.state.memory_last_err.is_empty() && !self.state.memory_last_ok.is_empty()
        });

        json!({
            "ok": true,
            "ver": self.state.ver,
            "coins": self.state.coins,
            "furniture": furniture,
            "character_pos": pos,
            "characters": chars,
            "available_characters": available,
            "hired_characters": hired_list,
            "hire_cost": HIRE_COST,
            "hire_slots": HIRE_SLOTS,
            "her": {
                "place": her_place,
                "doing": her_doing,
                "floor": her_floor,
                "text": her_text,
                "energy": her_char.as_ref().map(|c| c.energy).unwrap_or(0),
                "mood": her_char.as_ref().map(|c| c.mood).unwrap_or(0),
                "full": her_char.as_ref().map(|c| c.full).unwrap_or(0),
                "clean": her_char.as_ref().map(|c| c.clean).unwrap_or(0)
            },
            "move_speed": MOVE_SPEED,
            "plots": plots,
            "warehouse": &self.state.warehouse,
            "items": &self.state.items,
            "item_prices": ITEM_PRICE.iter().map(|(k, v)| (k.to_string(), *v)).collect::<BTreeMap<String, i64>>(),
            "ledger": self.state.ledger.iter().rev().take(120).cloned().collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>(),
            "seeds": &self.state.seeds,
            "staff": staff,
            "phase": phase,
            "weather": {
                "kind": &self.state.weather_kind,
                "name": &self.state.weather_name,
                "icon": &self.state.weather_icon,
                "temp": self.state.weather_temp
            },
            "tasks": &self.state.tasks,
            "work_history": &self.state.work_history,
            "layout": &self.state.layout,
            "diary": diary,
            "memory": memory_status,
            "events": self.state.events.iter().rev().take(60).cloned().collect::<Vec<_>>(),
            "notes": self.state.notes.iter().take(40).cloned().collect::<Vec<_>>(),
            "last_adjust_at": &self.state.last_adjust_at,
            "next_adjust_at": next_adjust,
            "adjust_interval_hours": 3,
            "last_tick": &self.state.last_tick,
            "now": fmt_ts(now)
        })
    }

    pub fn status(&mut self) -> Value {
        self.maybe_tick();
        let now = now_ts();
        let next_adjust = parse_ts(&self.state.last_adjust_at)
            .map(|t| fmt_ts(t + ADJUST_SECS))
            .unwrap_or_default();
        json!({
            "ok": true,
            "phase": phase_of(now),
            "weather": {
                "kind": &self.state.weather_kind,
                "name": &self.state.weather_name,
                "icon": &self.state.weather_icon,
                "temp": self.state.weather_temp
            },
            "coins": self.state.coins,
            "warehouse": &self.state.warehouse,
            "staff": self.state.staff.len(),
            "last_adjust_at": &self.state.last_adjust_at,
            "next_adjust_at": next_adjust,
            "now": fmt_ts(now)
        })
    }

    // ---------------- 动作 ----------------

    pub fn action(&mut self, body: &Value) -> Value {
        let typ = body.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let now = now_ts();
        let result: Result<Value, String> = match typ {
            "plant" => {
                let plot = body.get("plot").and_then(|v| v.as_str()).unwrap_or("");
                let crop = body.get("crop").and_then(|v| v.as_str()).unwrap_or("");
                self.do_plant(plot, crop, now)
            }
            "water" => {
                let plot = body.get("plot").and_then(|v| v.as_str()).unwrap_or("");
                self.do_water(plot, now)
            }
            "fertilize" => {
                let plot = body.get("plot").and_then(|v| v.as_str()).unwrap_or("");
                self.do_fertilize(plot, now)
            }
            "harvest" => {
                let plot = body.get("plot").and_then(|v| v.as_str()).unwrap_or("");
                self.do_harvest(plot, now)
            }
            "sell" => {
                let crop = body.get("crop").and_then(|v| v.as_str()).unwrap_or("");
                let qty = body.get("qty").and_then(|v| v.as_i64()).unwrap_or(1);
                self.do_sell(crop, qty, now)
            }
            "buy_seed" => {
                let crop = body.get("crop").and_then(|v| v.as_str()).unwrap_or("");
                let qty = body.get("qty").and_then(|v| v.as_i64()).unwrap_or(1);
                self.do_buy_seed(crop, qty, now)
            }
            "hire" => {
                let id = body
                    .get("id")
                    .and_then(|v| v.as_str())
                    .or_else(|| body.get("name").and_then(|v| v.as_str()))
                    .unwrap_or("");
                self.do_hire_char(id, now)
            }
            "fire" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                self.do_fire_char(id, now)
            }
            "assign_work" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let job = body.get("job").and_then(|v| v.as_str()).unwrap_or("");
                self.do_assign_work(id, job, now)
            }
            "cancel_work" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                self.do_cancel_work(id)
            }
            "buy_furniture" => {
                let kind = body.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                let floor = body.get("floor").and_then(|v| v.as_u64()).unwrap_or(1) as u8;
                let x = body.get("x").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
                let y = body.get("y").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
                self.do_buy_furniture(kind, floor, x, y, now)
            }
            "move_furniture" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let floor = body.get("floor").and_then(|v| v.as_u64()).unwrap_or(1) as u8;
                let x = body.get("x").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
                let y = body.get("y").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
                self.do_move_furniture(id, floor, x, y, now)
            }
            "assign" => {
                let title = body.get("title").and_then(|v| v.as_str()).unwrap_or("整理屋子");
                let staff_id = body.get("staff_id").and_then(|v| v.as_str()).unwrap_or("");
                self.do_assign(title, staff_id, now)
            }
            "lock" | "unlock" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                self.do_lock(id, typ == "lock", now)
            }
            "set_avatar" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let avatar = body.get("avatar").and_then(|v| v.as_str()).unwrap_or("");
                self.do_set_avatar(id, avatar, now)
            }
            "clear_avatar" => {
                let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
                self.do_clear_avatar(id, now)
            }
            "care" => {
                let job = body.get("job").and_then(|v| v.as_str()).unwrap_or("bathe");
                self.do_care(job, now)
            }
            "gather" => {
                let place = body.get("place").and_then(|v| v.as_str()).unwrap_or("hill");
                self.do_gather(place, now)
            }
            "craft" => {
                let recipe = body.get("recipe").and_then(|v| v.as_str()).unwrap_or("");
                self.do_craft(recipe, now)
            }
            "sell_item" => {
                let item = body.get("item").and_then(|v| v.as_str()).unwrap_or("");
                let qty = body.get("qty").and_then(|v| v.as_i64()).unwrap_or(1);
                self.do_sell_item(item, qty, now)
            }
            "adjust_now" => self.do_adjust_now(now),
            other => Err(format!("不认识这个动作:{}", other)),
        };

        match result {
            Ok(v) => {
                let _ = self.save();
                json!({
                    "ok": true,
                    "result": v,
                    "coins": self.state.coins,
                    "warehouse": &self.state.warehouse,
                    "staff": &self.state.staff,
                    "events": self.state.events.iter().rev().take(10).cloned().collect::<Vec<_>>()
                })
            }
            Err(e) => json!({ "ok": false, "error": e }),
        }
    }

    fn plot_index(&self, id: &str) -> Result<usize, String> {
        self.state
            .plots
            .iter()
            .position(|p| p.id == id)
            .ok_or_else(|| format!("没有这块地:{}", id))
    }

    fn do_plant(&mut self, plot_id: &str, crop: &str, now: i64) -> Result<Value, String> {
        let idx = self.plot_index(plot_id)?;
        if CROPS.iter().all(|c| c.0 != crop) {
            return Err(format!("不认识这个作物:{}", crop));
        }
        if self.state.plots[idx].crop.is_some() {
            return Err("这块地已经种了东西".into());
        }
        let cname = crop_name(crop);
        let seeds = self.state.seeds.get(crop).copied().unwrap_or(0);
        let how;
        if seeds > 0 {
            self.state.seeds.insert(crop.to_string(), seeds - 1);
            how = "用库存种子";
        } else if self.state.coins >= 3 {
            self.state.coins -= 3;
            self.state.ledger.push(json!({
                "at": fmt_ts(now), "type": "seed", "amount": -3,
                "note": format!("买{}种子", cname), "balance": self.state.coins
            }));
            how = "现买种子";
        } else {
            return Err("没种子也没钱".into());
        }
        {
            let p = &mut self.state.plots[idx];
            p.crop = Some(crop.to_string());
            p.planted_at = Some(fmt_ts(now));
            p.watered_at = None;
            p.fertilized_at = None;
        }
        let who = self.who_main();
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "plant".into(),
            text: format!(
                "{}在{}种了{}({})",
                who,
                if self.state.plots[idx].floor == 2 { "温室" } else { "大田" },
                cname,
                how
            ),
            who,
            source: "farm".into(),
            ..Default::default()
        });
        Ok(json!({ "plot": plot_id, "crop": crop, "crop_name": cname, "how": how }))
    }

    fn do_water(&mut self, plot_id: &str, now: i64) -> Result<Value, String> {
        let idx = self.plot_index(plot_id)?;
        if self.state.plots[idx].crop.is_none() {
            return Err("这块地还没种东西".into());
        }
        self.state.plots[idx].watered_at = Some(fmt_ts(now));
        let cname = crop_name(self.state.plots[idx].crop.as_deref().unwrap_or(""));
        let who = self.who_main();
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "water".into(),
            text: format!("{}给{}浇了水", who, cname),
            who,
            source: "farm".into(),
            ..Default::default()
        });
        Ok(json!({ "plot": plot_id, "crop": cname }))
    }

    fn do_fertilize(&mut self, plot_id: &str, now: i64) -> Result<Value, String> {
        let idx = self.plot_index(plot_id)?;
        if self.state.plots[idx].crop.is_none() {
            return Err("这块地还没种东西".into());
        }
        if self.state.plots[idx].fertilized_at.is_some() {
            return Err("这块地已经施过肥了".into());
        }
        if self.state.coins < 2 {
            return Err("金币不够买肥料".into());
        }
        self.state.coins -= 2;
        self.state.plots[idx].fertilized_at = Some(fmt_ts(now));
        let cname = crop_name(self.state.plots[idx].crop.as_deref().unwrap_or(""));
        let who = self.who_main();
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "fertilize", "amount": -2,
            "note": format!("给{}施肥", cname), "balance": self.state.coins
        }));
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "fertilize".into(),
            text: format!("{}给{}施了肥,能早熟一点", who, cname),
            who,
            source: "farm".into(),
            ..Default::default()
        });
        Ok(json!({ "plot": plot_id, "crop": cname }))
    }

    fn do_harvest(&mut self, plot_id: &str, now: i64) -> Result<Value, String> {
        let idx = self.plot_index(plot_id)?;
        let mature = self.mature(&self.state.plots[idx], now);
        if !mature {
            return Err("还没熟".into());
        }
        let crop = self.state.plots[idx].crop.clone().unwrap_or_default();
        let cname = crop_name(&crop);
        {
            let p = &mut self.state.plots[idx];
            p.crop = None;
            p.planted_at = None;
            p.watered_at = None;
            p.fertilized_at = None;
            p.harvests += 1;
        }
        let cnt = self.state.warehouse.get(&cname).copied().unwrap_or(0) + 1;
        self.state.warehouse.insert(cname.clone(), cnt);
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "harvest", "amount": 0,
            "note": format!("收获 {} ×1", cname), "balance": self.state.coins
        }));
        let who = self.who_main();
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "harvest".into(),
            text: format!("{}收了 1 份{},放进仓库", who, cname),
            who,
            source: "farm".into(),
            ..Default::default()
        });
        Ok(json!({ "plot": plot_id, "crop": cname, "warehouse": self.state.warehouse }))
    }

    fn do_care(&mut self, job: &str, now: i64) -> Result<Value, String> {
        let name = self.state.characters.first().map(|c| c.name.clone()).unwrap_or_default();
        let mut msg = String::new();
        let mut snapshot = (0i32, 0i32, 0i32);
        {
            let c = match self.state.characters.first_mut() {
                Some(c) => c,
                None => return Err("还没有人".into()),
            };
            match job {
                "bathe" => {
                    c.clean = (c.clean + 40).min(100);
                    c.mood = (c.mood + 6).min(100);
                    msg = format!("{}去洗了个熱水澡,清洁 {}", name, c.clean);
                }
                "eat" => {
                    c.full = (c.full + 40).min(100);
                    c.mood = (c.mood + 5).min(100);
                    msg = format!("{}吃了一顿热乎乎的,饱腹 {}", name, c.full);
                }
                "play" => {
                    if c.energy < 6 {
                        return Err("她太累了,先让她休息一下".into());
                    }
                    c.energy -= 6;
                    c.mood = (c.mood + 28).min(100);
                    msg = format!("陪{}玩了一会儿,心情 {}", name, c.mood);
                }
                other => return Err(format!("不认识这种照顾:{}", other)),
            }
            snapshot = (c.mood, c.full, c.clean);
        }
        let who = name.clone();
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "care".into(),
            text: msg.clone(),
            who,
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "text": msg, "mood": snapshot.0, "full": snapshot.1, "clean": snapshot.2 }))
    }

    fn do_gather(&mut self, place: &str, now: i64) -> Result<Value, String> {
        let table: &[(&str, i64)] = if place == "sea" { GATHER_SEA } else { GATHER_HILL };
        let last = parse_ts(&self.state.last_gather_at).unwrap_or(0);
        if last > 0 && now - last < 1200 {
            let left = (1200 - (now - last)) / 60;
            return Err(format!("刚采过,过 {} 分钟再去", left.max(1)));
        }
        {
            let c = match self.state.characters.first_mut() {
                Some(c) => c,
                None => return Err("还没有人".into()),
            };
            if c.energy < 8 {
                return Err("能量不够,先让她休息".into());
            }
            c.energy -= 8;
        }
        let mut rng = Rng::new(seed_now());
        let n = if rng.chance(0.35) { 2 } else { 1 };
        let mut got: Vec<(String, i64)> = Vec::new();
        for _ in 0..n {
            let roll = (rng.next() % 100) as i64;
            let mut acc = 0;
            let mut chosen = table[0].0;
            for (name, w) in table.iter() {
                acc += *w;
                if roll < acc {
                    chosen = *name;
                    break;
                }
            }
            let e = self.state.items.entry(chosen.to_string()).or_insert(0);
            *e += 1;
            if let Some(slot) = got.iter_mut().find(|(k, _)| k == chosen) {
                slot.1 += 1;
            } else {
                got.push((chosen.to_string(), 1));
            }
        }
        self.state.last_gather_at = fmt_ts(now);
        let who = self.who_main();
        let text = format!(
            "{}去{}采了一轮:{}",
            who,
            if place == "sea" { "海边" } else { "后山" },
            got.iter().map(|(k, v)| format!("{}{}", k, v)).collect::<Vec<_>>().join(" ")
        );
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "gather".into(),
            text: text.clone(),
            who,
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "text": text, "got": got.iter().map(|(k, v)| json!({"item": k, "qty": v})).collect::<Vec<_>>(), "items": &self.state.items }))
    }

    fn do_craft(&mut self, recipe: &str, now: i64) -> Result<Value, String> {
        let found = RECIPES.iter().find(|r| r.0 == recipe);
        let (_, name, need) = match found {
            Some(r) => *r,
            None => return Err(format!("不认识这个配方:{}", recipe)),
        };
        for (item, qty) in need.iter() {
            let have = self.state.items.get(*item).copied().unwrap_or(0);
            if have < *qty {
                return Err(format!("缺材料:{} {}/{}", item, have, qty));
            }
        }
        for (item, qty) in need.iter() {
            let e = self.state.items.entry(item.to_string()).or_insert(0);
            *e -= *qty;
            if *e <= 0 {
                *e = 0;
            }
        }
        let e = self.state.items.entry(name.to_string()).or_insert(0);
        *e += 1;
        let who = self.who_main();
        let text = format!("{}做好了一件{}", who, name);
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "craft".into(),
            text: text.clone(),
            who,
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "text": text, "item": name, "items": &self.state.items }))
    }

    fn do_sell_item(&mut self, item: &str, qty: i64, now: i64) -> Result<Value, String> {
        let item = match item {
            "plank" => "木板",
            "herb_pack" => "草药包",
            "pearl_necklace" => "珍珠项链",
            other => other,
        };
        let qty = qty.max(1);
        let price = ITEM_PRICE.iter().find(|x| x.0 == item).map(|x| x.1).unwrap_or(0);
        if price <= 0 {
            return Err(format!("这个东西卖不了:{}", item));
        }
        let have = self.state.items.get(item).copied().unwrap_or(0);
        if have < qty {
            return Err(format!("物品柜里{}只有 {} 份", item, have));
        }
        let e = self.state.items.entry(item.to_string()).or_insert(0);
        *e -= qty;
        let gain = price * qty;
        self.state.coins += gain;
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "sell_item", "amount": gain,
            "note": format!("卖出 {} ×{}", item, qty), "balance": self.state.coins
        }));
        let who = self.who_main();
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "sell".into(),
            text: format!("{}卖了 {} 份{},进账 {} 金币", who, qty, item, gain),
            who,
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "item": item, "qty": qty, "gain": gain, "items": &self.state.items }))
    }

    fn do_sell(&mut self, crop: &str, qty: i64, now: i64) -> Result<Value, String> {
        let qty = qty.max(1);
        let key = if self.state.warehouse.contains_key(crop) {
            crop.to_string()
        } else {
            crop_name(crop)
        };
        let have = self.state.warehouse.get(&key).copied().unwrap_or(0);
        if have < qty {
            return Err(format!("仓库里{}只有 {} 份", key, have));
        }
        self.state.warehouse.insert(key.clone(), have - qty);
        let gain = qty * 5;
        self.state.coins += gain;
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "sell", "amount": gain,
            "note": format!("卖出 {} ×{}", key, qty), "balance": self.state.coins
        }));
        let who = self.who_main();
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "sell".into(),
            text: format!("{}卖了 {} 份{},进账 {} 金币", who, qty, key, gain),
            who,
            source: "farm".into(),
            ..Default::default()
        });
        Ok(json!({ "crop": key, "qty": qty, "gain": gain }))
    }

    fn do_buy_seed(&mut self, crop: &str, qty: i64, now: i64) -> Result<Value, String> {
        let qty = qty.max(1);
        if CROPS.iter().all(|c| c.0 != crop) {
            return Err(format!("不认识这个作物:{}", crop));
        }
        let cost = qty * 2;
        if self.state.coins < cost {
            return Err("金币不够买种子".into());
        }
        self.state.coins -= cost;
        let n = self.state.seeds.get(crop).copied().unwrap_or(0) + qty;
        self.state.seeds.insert(crop.to_string(), n);
        let cname = crop_name(crop);
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "seed", "amount": -cost,
            "note": format!("买 {} 种子 ×{}", cname, qty), "balance": self.state.coins
        }));
        Ok(json!({ "crop": crop, "qty": qty, "cost": cost, "seeds": self.state.seeds }))
    }

    fn do_hire(&mut self, name: &str, wage: i64, now: i64) -> Result<Value, String> {
        let idx = self.state.staff.len();
        let id = format!("staff_{}", idx + 1);
        let mut rng = Rng::new(seed_now());
        let floor = if rng.chance(0.5) { 1 } else { 2 };
        let cell = self.free_cell(floor, &mut rng).unwrap_or((2.0, 4.0));
        let s = Staff {
            id: id.clone(),
            name: name.to_string(),
            role: "帮手".into(),
            wage: wage.max(0),
            hired_at: fmt_ts(now),
            look: look_for_index(idx).to_string(),
            avatar: None,
            avatar_source: "pixel".into(),
            floor,
            x: cell.0,
            y: cell.1,
            task: "刚搬进来".into(),
        };
        self.state.staff.push(s.clone());
        self.state.ledger.push(json!({
            "at": fmt_ts(now), "type": "hire", "amount": 0,
            "note": format!("雇佣 {}(日薪 {})", name, wage), "balance": self.state.coins
        }));
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "hire".into(),
            text: format!("{}雇了 {} 来帮忙(日薪 {})", self.who_main(), name, wage),
            who: self.who_main(),
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "staff": s }))
    }

    fn do_fire(&mut self, id: &str, now: i64) -> Result<Value, String> {
        let before = self.state.staff.len();
        let name = self
            .state
            .staff
            .iter()
            .find(|s| s.id == id)
            .map(|s| s.name.clone())
            .unwrap_or_else(|| id.to_string());
        self.state.staff.retain(|s| s.id != id);
        if self.state.staff.len() == before {
            return Err(format!("没有这个雇工:{}", id));
        }
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "fire".into(),
            text: format!("{}让 {} 回家了", self.who_main(), name),
            who: self.who_main(),
            source: "home".into(),
            ..Default::default()
        });
        Ok(json!({ "staff": self.state.staff }))
    }

    fn do_assign(&mut self, title: &str, staff_id: &str, now: i64) -> Result<Value, String> {
        let id = format!("task_{}", self.state.tasks.len() + 1);
        let task = json!({
            "id": id,
            "title": title,
            "assigned_to": staff_id,
            "status": "todo",
            "created_at": fmt_ts(now),
            "done_at": null,
            "done_by": ""
        });
        self.state.tasks.push(task.clone());
        if self.state.tasks.len() > 200 {
            let cut = self.state.tasks.len() - 200;
            self.state.tasks.drain(0..cut);
        }
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "assign".into(),
            text: format!("{}派了件活:{}", self.who_main(), title),
            who: self.who_main(),
            source: "home".into(),
            ..Default::default()
        });
        Ok(task)
    }

    fn do_lock(&mut self, id: &str, locked: bool, now: i64) -> Result<Value, String> {
        let pos = self
            .state
            .furniture
            .iter()
            .position(|f| f.id == id)
            .ok_or_else(|| format!("屋里没有这件东西:{}", id))?;
        let name = cat_name(&self.state.furniture[pos].kind).to_string();
        self.state.furniture[pos].locked = locked;
        self.push_event(Event {
            at: fmt_ts(now),
            kind: if locked { "furniture_lock" } else { "furniture_unlock" }.into(),
            text: format!(
                "{}把{}标记为{}",
                self.who_main(),
                name,
                if locked { "不要自动移动" } else { "可移动" }
            ),
            who: self.who_main(),
            source: "home".into(),
            furniture_id: id.to_string(),
            furniture: name,
            ..Default::default()
        });
        Ok(json!({ "id": id, "locked": locked }))
    }

    /// 换头像:data URL(相册/本地图片)。角色和雇工都能换。
    fn do_set_avatar(&mut self, id: &str, avatar: &str, now: i64) -> Result<Value, String> {
        let avatar = avatar.trim();
        if !avatar.starts_with("data:image/") {
            return Err("头像必须是 data:image/... 的图片数据".into());
        }
        if avatar.len() > 3_000_000 {
            return Err("头像图片太大(超过约 3MB),换张小一点的".into());
        }
        if let Some(pos) = self.state.characters.iter().position(|c| c.id == id) {
            let name = self.state.characters[pos].name.clone();
            self.state.characters[pos].avatar = Some(avatar.to_string());
            self.state.characters[pos].avatar_source = "album".into();
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "avatar_change".into(),
                text: format!("{}从相册换了一张新头像", name),
                who: name,
                source: "home".into(),
                ..Default::default()
            });
            return Ok(json!({ "id": id, "avatar_source": "album", "who": "character" }));
        }
        if let Some(pos) = self.state.staff.iter().position(|s| s.id == id) {
            let name = self.state.staff[pos].name.clone();
            self.state.staff[pos].avatar = Some(avatar.to_string());
            self.state.staff[pos].avatar_source = "album".into();
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "avatar_change".into(),
                text: format!("{}从相册换了一张新头像", name),
                who: name,
                source: "home".into(),
                ..Default::default()
            });
            return Ok(json!({ "id": id, "avatar_source": "album", "who": "staff" }));
        }
        Err(format!("没有这个角色:{}", id))
    }

    fn do_clear_avatar(&mut self, id: &str, now: i64) -> Result<Value, String> {
        if let Some(pos) = self.state.characters.iter().position(|c| c.id == id) {
            let name = self.state.characters[pos].name.clone();
            self.state.characters[pos].avatar = None;
            self.state.characters[pos].avatar_source = "pixel".into();
            self.push_event(Event {
                at: fmt_ts(now),
                kind: "avatar_change".into(),
                text: format!("{}把头像换回默认像素形象", name),
                who: name,
                source: "home".into(),
                ..Default::default()
            });
            return Ok(json!({ "id": id, "avatar_source": "pixel" }));
        }
        if let Some(pos) = self.state.staff.iter().position(|s| s.id == id) {
            self.state.staff[pos].avatar = None;
            self.state.staff[pos].avatar_source = "pixel".into();
            return Ok(json!({ "id": id, "avatar_source": "pixel" }));
        }
        Err(format!("没有这个角色:{}", id))
    }

    fn do_buy_furniture(&mut self, kind: &str, floor: u8, x: i32, y: i32, now: i64) -> Result<Value, String> {
        if !CATALOG.iter().any(|c| c.0 == kind) {
            return Err(format!("没有这种家具:{}", kind));
        }
        let price = furn_price(kind);
        if self.state.coins < price {
            return Err(format!("金币不够,{} 要 {} 金币", cat_name(kind), price));
        }
        self.can_place(kind, floor, x, y, None)?;
        self.state.coins -= price;
        let nows = fmt_ts(now);
        let id = format!("{}{}", kind, self.next_item_num());
        self.state.furniture.push(Item {
            id: id.clone(),
            kind: kind.to_string(),
            floor,
            x,
            y,
            added_at: nows.clone(),
            facing: "south".into(),
            locked: false,
            moved_at: nows.clone(),
            used_count: 0,
            last_used_at: String::new(),
        });
        self.state.ledger.push(json!({
            "at": nows, "type": "buy_furniture", "amount": -price,
            "note": format!("买了一件{}", cat_name(kind)), "balance": self.state.coins
        }));
        self.push_event(Event {
            at: nows,
            kind: "furniture_add".into(),
            text: format!("鲸鱼娘花 {} 金币买了一件{},摆在{}。", price, cat_name(kind), room_name(floor, x, y)),
            who: "鲸鱼娘".into(),
            source: "home".into(),
            furniture_id: id.clone(),
            furniture: cat_name(kind).to_string(),
            to: Some(Coord { floor, x, y }),
            reason: "用户手动添置".into(),
            new_item: true,
            ..Default::default()
        });
        Ok(json!({ "id": id, "price": price, "coins": self.state.coins }))
    }

    fn do_move_furniture(&mut self, id: &str, floor: u8, x: i32, y: i32, now: i64) -> Result<Value, String> {
        let pos = self
            .state
            .furniture
            .iter()
            .position(|f| f.id == id)
            .ok_or_else(|| format!("屋里没有这件东西:{}", id))?;
        let kind = self.state.furniture[pos].kind.clone();
        self.can_place(&kind, floor, x, y, Some(id))?;
        let from = Coord {
            floor: self.state.furniture[pos].floor,
            x: self.state.furniture[pos].x,
            y: self.state.furniture[pos].y,
        };
        {
            let f = &mut self.state.furniture[pos];
            f.floor = floor;
            f.x = x;
            f.y = y;
            f.moved_at = fmt_ts(now);
        }
        self.push_event(Event {
            at: fmt_ts(now),
            kind: "furniture_move".into(),
            text: format!(
                "鲸鱼娘把{}从{}（{},{}）挪到{}（{},{}）。",
                cat_name(&kind), room_name(from.floor, from.x, from.y), from.x, from.y,
                room_name(floor, x, y), x, y
            ),
            who: "鲸鱼娘".into(),
            source: "home".into(),
            furniture_id: id.to_string(),
            furniture: cat_name(&kind).to_string(),
            from: Some(from),
            to: Some(Coord { floor, x, y }),
            reason: "用户手动摆放".into(),
            ..Default::default()
        });
        Ok(json!({ "id": id, "floor": floor, "x": x, "y": y }))
    }

    fn do_adjust_now(&mut self, now: i64) -> Result<Value, String> {
        let ev = self.adjust_once(now, "手动");
        self.state.last_adjust_at = fmt_ts(now);
        Ok(json!({ "event": ev, "last_adjust_at": self.state.last_adjust_at }))
    }

    pub fn force_adjust(&mut self) -> Value {
        let now = now_ts();
        let ev = self.adjust_once(now, "验收演示");
        self.state.last_adjust_at = fmt_ts(now);
        let _ = self.save();
        json!({ "ok": true, "event": ev, "furniture": self.state.furniture.len(), "now": fmt_ts(now) })
    }
}

// ============================================================
// 状态文件 / seed
// ============================================================

pub fn default_state_path() -> PathBuf {
    if let Ok(p) = std::env::var("RIVULET_HOME_STATE") {
        if !p.trim().is_empty() {
            return PathBuf::from(p);
        }
    }
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("data").join("home.json")))
        .unwrap_or_else(|| PathBuf::from("data/home.json"))
}

fn seed_state() -> State {
    // 编译期嵌入种子:运行时不依赖 项目-AI平台/home/data
    match serde_json::from_str::<State>(include_str!("home_seed.json")) {
        Ok(s) => s,
        Err(_) => State {
            ver: 1,
            coins: 120,
            ..Default::default()
        },
    }
}

// ============================================================
// Tauri 命令(在 lib.rs 里注册)
// ============================================================

pub struct HomeStore(pub Arc<Mutex<HomeEngine>>);

fn lock_engine<'a>(store: &'a TauriState<'_, HomeStore>) -> std::sync::MutexGuard<'a, HomeEngine> {
    store.0.lock().unwrap_or_else(|e| e.into_inner())
}

#[tauri::command]
pub async fn home_snapshot(store: TauriState<'_, HomeStore>) -> Result<Value, String> {
    let mut e = lock_engine(&store);
    // 面板每 250ms 轮询一次:让轮询也驱动引擎 tick(每秒最多一次),否则只有后台 20s 线程在走
    e.maybe_tick();
    Ok(e.snapshot())
}

#[tauri::command]
pub async fn home_action(body: Value, store: TauriState<'_, HomeStore>) -> Result<Value, String> {
    let mut e = lock_engine(&store);
    Ok(e.action(&body))
}

#[tauri::command]
pub async fn home_status(store: TauriState<'_, HomeStore>) -> Result<Value, String> {
    let mut e = lock_engine(&store);
    Ok(e.status())
}

#[tauri::command]
pub async fn home_adjust_now(store: TauriState<'_, HomeStore>) -> Result<Value, String> {
    let mut e = lock_engine(&store);
    Ok(e.force_adjust())
}

// ============================================================
// 测试(不需要 Tauri 运行)
// ============================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_engine(tag: &str) -> HomeEngine {
        let dir = std::env::temp_dir().join(format!("rivulet_home_test_{}_{}", tag, seed_now()));
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("home.json");
        HomeEngine::open(path)
    }

    #[test]
    fn seed_imports_23_furniture_14_plots() {
        let e = tmp_engine("seed");
        assert_eq!(e.state.furniture.len(), 23, "家具应为 23 件");
        assert_eq!(e.state.plots.len(), 14, "地块应为 14 块");
        assert!(!e.state.characters.is_empty(), "角色不能为空");
        assert!(e.state.furniture.iter().all(|f| f.floor == 1 || f.floor == 2));
        let kinds: std::collections::BTreeSet<&str> =
            e.state.furniture.iter().map(|f| f.kind.as_str()).collect();
        assert!(kinds.len() >= 15, "家具种类应超过 15 种,实际 {}", kinds.len());
    }

    #[test]
    fn placement_rules_reject_overlap_bounds_and_stairs() {
        let e = tmp_engine("rules");
        // 越界
        assert!(e.can_place("plant", 1, 12, 7, None).is_err(), "越界应被拒");
        // 楼梯口
        assert!(e.can_place("plant", 1, 11, 5, None).is_err(), "楼梯口应被拒");
        // 门口(一层玄关)
        assert!(e.can_place("plant", 1, 9, 7, None).is_err(), "门口应被拒");
        // 和电视重叠(tv 在 1,0,占 3 格)
        assert!(e.can_place("plant", 1, 1, 0, None).is_err(), "重叠应被拒");
        // 房间不匹配:冰箱放客厅
        assert!(e.can_place("fridge", 1, 2, 2, None).is_err(), "冰箱不该放客厅");
        // 合法
        assert!(e.can_place("plant", 1, 2, 5, None).is_ok(), "空位应放得下");
    }

    #[test]
    fn furniture_adjust_changes_scene_and_records_reason() {
        let mut e = tmp_engine("adjust");
        let before: Vec<(String, i32, i32, String)> = e
            .state
            .furniture
            .iter()
            .map(|f| (f.id.clone(), f.x, f.y, f.facing.clone()))
            .collect();
        let mut changed = false;
        for _ in 0..20 {
            let ev = e.adjust_once(now_ts(), "测试");
            assert!(ev.is_some(), "每次整理都应给出事件");
            let ev = ev.unwrap();
            assert!(!ev.text.is_empty(), "事件要有内容");
            assert!(!ev.reason.is_empty(), "事件要有原因");
            assert!(!ev.who.is_empty(), "事件要写明是谁做的");
            match ev.kind.as_str() {
                "furniture_move" | "furniture_add" | "furniture_rotate" => {
                    assert!(!ev.furniture_id.is_empty(), "家具事件要带家具 id");
                    changed = true;
                }
                _ => {}
            }
        }
        let after: Vec<(String, i32, i32, String)> = e
            .state
            .furniture
            .iter()
            .map(|f| (f.id.clone(), f.x, f.y, f.facing.clone()))
            .collect();
        assert!(changed, "20 次整理里应至少真的动过家具");
        assert_ne!(before, after, "场景数据必须真实变化");
    }

    #[test]
    fn offline_catchup_triggers_and_does_not_repeat() {
        let mut e = tmp_engine("catchup");
        let now = now_ts();
        e.state.last_adjust_at = fmt_ts(now - 7 * 3600); // 离线 7 小时 = 2 个周期
        e.state.last_tick = fmt_ts(now - 7 * 3600);
        let events = e.tick_at(now);
        let has_adjust = events
            .iter()
            .any(|x| x.kind.starts_with("furniture_") || x.kind == "room_check");
        assert!(has_adjust, "离线跨 7 小时应触发整理,实际事件: {:?}", events.iter().map(|e| &e.kind).collect::<Vec<_>>());
        let after = parse_ts(&e.state.last_adjust_at).unwrap();
        assert!(now - after < 10, "补算后 last_adjust_at 应推进到当前");
        // 紧接着再 tick 不应重复触发
        let before_len = e.state.events.len();
        e.tick_at(now + 5);
        let new_adjust = e.state.events[before_len..]
            .iter()
            .filter(|x| x.kind.starts_with("furniture_"))
            .count();
        assert_eq!(new_adjust, 0, "同一周期内不能重复整理");
    }

    #[test]
    fn actions_work_and_persist() {
        let mut e = tmp_engine("actions");
        // 种子数据里这块地已经种着番茄,先清空再测播种
        e.state.plots[0].crop = None;
        e.state.plots[0].planted_at = None;
        e.state.plots[0].watered_at = None;
        let plot = e.state.plots[0].id.clone();
        let r = e.action(&json!({"type":"plant","plot":plot,"crop":"tomato"}));
        assert_eq!(r["ok"], true, "播种应成功: {}", r);
        let r = e.action(&json!({"type":"water","plot":plot}));
        assert_eq!(r["ok"], true, "浇水应成功: {}", r);
        let crop = e.state.plots[0].crop.clone().unwrap();
        assert_eq!(crop, "tomato");
        // 直接改 planting 时间,让作物成熟后收获
        e.state.plots[0].planted_at = Some(fmt_ts(now_ts() - 10 * 3600));
        let r = e.action(&json!({"type":"harvest","plot":plot}));
        assert_eq!(r["ok"], true, "收获应成功: {}", r);
        assert!(e.state.warehouse.get("番茄").copied().unwrap_or(0) >= 1);
        // 重开:状态要还在
        e.save().unwrap();
        let mut e2 = HomeEngine::open(e.path.clone());
        assert!(e2.state.warehouse.get("番茄").copied().unwrap_or(0) >= 1, "重开后仓库应保留");
        assert_eq!(e2.state.plots[0].crop, None, "收获后地块应为空");
        // 换成自定义头像并存盘
        let r = e2.action(&json!({
            "type": "set_avatar",
            "id": "jingyu",
            "avatar": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
        }));
        assert_eq!(r["ok"], true, "换头像应成功: {}", r);
        let reopened = HomeEngine::open(e2.path.clone());
        let jingyu = reopened
            .state
            .characters
            .iter()
            .find(|c| c.id == "jingyu")
            .unwrap();
        assert!(jingyu.avatar.is_some(), "自定义头像要能持久化");
        assert_eq!(jingyu.avatar_source, "album");
    }

    #[test]
    fn locked_furniture_never_moves() {
        let mut e = tmp_engine("lock");
        let plant = e
            .state
            .furniture
            .iter()
            .find(|f| f.kind == "plant")
            .cloned()
            .unwrap();
        {
            let pos = e.state.furniture.iter().position(|f| f.id == plant.id).unwrap();
            e.state.furniture[pos].locked = true;
        }
        for _ in 0..30 {
            e.adjust_once(now_ts(), "测试");
        }
        let after = e
            .state
            .furniture
            .iter()
            .find(|f| f.kind == "plant")
            .unwrap();
        assert_eq!((after.x, after.y), (plant.x, plant.y), "锁定家具不能被动");
    }

    #[test]
    fn actions_return_ok_false_instead_of_panic() {
        let mut e = tmp_engine("errors");
        let r = e.action(&json!({"type":"plant","plot":"不存在","crop":"tomato"}));
        assert_eq!(r["ok"], false);
        assert!(r["error"].as_str().unwrap_or("").contains("没有这块地"));
        e.state.plots[0].crop = None;
        e.state.plots[0].planted_at = None;
        let plot = e.state.plots[0].id.clone();
        let r = e.action(&json!({"type":"harvest","plot":plot}));
        assert_eq!(r["ok"], false, "空地块收获应返回 ok:false");
    }

    #[test]
    fn multi_character_positions_exist() {
        let mut e = tmp_engine("chars");
        let snap = e.snapshot();
        let arr = snap["characters"].as_array().unwrap();
        assert!(arr.len() >= 2, "至少两个角色");
        for c in arr {
            assert!(c["look"].as_str().unwrap_or("").len() > 0, "角色要有像素形象 key");
            assert!(c["floor"].as_u64().unwrap_or(0) == 1 || c["floor"].as_u64().unwrap_or(0) == 2);
        }
    }

    #[test]
    fn avatar_from_album_can_change_and_revert() {
        let mut e = tmp_engine("avatar");
        let data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
        let r = e.action(&json!({ "type": "set_avatar", "id": "jingyu", "avatar": data }));
        assert_eq!(r["ok"], true, "从相册换头像应成功: {}", r);
        let snap = e.snapshot();
        let c = snap["characters"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["id"] == "jingyu")
            .unwrap();
        assert_eq!(c["avatar_source"], "album");
        assert!(c["avatar"].as_str().unwrap_or("").starts_with("data:image/"));
        // 换回默认像素形象
        let r = e.action(&json!({ "type": "clear_avatar", "id": "jingyu" }));
        assert_eq!(r["ok"], true);
        let snap = e.snapshot();
        let c = snap["characters"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["id"] == "jingyu")
            .unwrap();
        assert!(c["avatar"].is_null(), "清空后应回退像素形象");
        assert_eq!(c["avatar_source"], "pixel");
        // 不是图片的数据要拒绝
        let r = e.action(&json!({ "type": "set_avatar", "id": "jingyu", "avatar": "hello" }));
        assert_eq!(r["ok"], false);
    }

    /// 验收演示:打印一次自动调整的「调整前 / 调整后 / 原因」
    #[test]
    fn demo_furniture_adjust_showcase() {
        let mut e = tmp_engine("demo");
        let listing = |items: &Vec<Item>| -> Vec<String> {
            items
                .iter()
                .filter(|f| is_movable(&f.kind))
                .map(|f| {
                    format!(
                        "{} · {}（{},{}）{}",
                        cat_name(&f.kind),
                        room_name(f.floor, f.x, f.y),
                        f.x,
                        f.y,
                        facing_cn(&f.facing)
                    )
                })
                .collect()
        };
        println!("\n===== 家具自动调整演示（seed 数据）=====");
        println!("【调整前】");
        for line in listing(&e.state.furniture) {
            println!("  - {}", line);
        }
        let r = e.force_adjust();
        println!("\n【事件】{}", serde_json::to_string_pretty(&r["event"]).unwrap_or_default());
        println!("\n【调整后】");
        for line in listing(&e.state.furniture) {
            println!("  - {}", line);
        }
        println!(
            "\n【一句话原因】{}",
            r["event"]["reason"].as_str().unwrap_or("(这次只是转了一圈)")
        );
        assert!(r["ok"].as_bool().unwrap_or(false));
    }

    #[test]
    fn roster_has_seven_and_only_whale_default() {
        let e = tmp_engine("roster");
        assert_eq!(e.state.characters.len(), 7, "应有 7 名角色");
        let jingyu = e.state.characters.iter().find(|c| c.id == "jingyu").unwrap();
        assert!(jingyu.hired, "鲸鱼娘初始拥有");
        assert_eq!(jingyu.hire_cost, 0);
        for c in e.state.characters.iter().filter(|c| c.id != "jingyu") {
            assert!(!c.hired, "{} 初始未雇佣", c.name);
            assert_eq!(c.hire_cost, 10, "{} 雇佣费 10", c.name);
        }
        let names: Vec<&str> = e.state.characters.iter().map(|c| c.name.as_str()).collect();
        for n in ["鲸鱼娘", "Claude", "ChatGPT", "泥泥", "花花", "海海", "薇薇"] {
            assert!(names.contains(&n), "缺少角色 {}", n);
        }
    }

    #[test]
    fn hire_costs_ten_once_and_rejects_duplicate_or_poor() {
        let mut e = tmp_engine("hire");
        e.state.coins = 120;
        let r = e.action(&json!({"type":"hire","id":"nini"}));
        assert_eq!(r["ok"], true, "雇佣应成功: {}", r);
        assert_eq!(e.state.coins, 110, "一次性扣 10 金币");
        let c = e.state.characters.iter().find(|c| c.id == "nini").unwrap();
        assert!(c.hired);
        // 重复雇佣不重复扣费
        let r = e.action(&json!({"type":"hire","id":"nini"}));
        assert_eq!(r["ok"], false);
        assert_eq!(e.state.coins, 110, "重复雇佣不能再扣钱");
        // 钱不够
        e.state.coins = 5;
        let r = e.action(&json!({"type":"hire","id":"huahua"}));
        assert_eq!(r["ok"], false);
        assert!(r["error"].as_str().unwrap_or("").contains("金币不足"));
        // 鲸鱼娘不能雇佣/解雇
        assert_eq!(e.action(&json!({"type":"hire","id":"jingyu"}))["ok"], false);
        assert_eq!(e.action(&json!({"type":"fire","id":"jingyu"}))["ok"], false);
        // 解雇正常
        e.state.coins = 20;
        let r = e.action(&json!({"type":"fire","id":"nini"}));
        assert_eq!(r["ok"], true, "解雇应成功: {}", r);
        assert!(!e.state.characters.iter().find(|c| c.id == "nini").unwrap().hired);
    }

    #[test]
    fn pathfinding_uses_stairs_and_avoids_furniture() {
        let e = tmp_engine("path");
        let path = e
            .find_path(Cell { floor: 1, x: 4, y: 2 }, Cell { floor: 2, x: 4, y: 2 })
            .expect("两层之间应该能走通");
        assert!(path.iter().any(|c| c.floor == 2), "路径必须跨到 2F");
        assert!(
            path.iter().any(|c| c.x == 11 && c.y >= 4 && c.y <= 6),
            "跨层必须经过楼梯口"
        );
        assert!(
            path.iter().all(|c| e.walkable(c.floor, c.x, c.y)),
            "路径不能穿过家具/墙"
        );
    }

    #[test]
    fn assigned_work_really_moves_and_changes_plot() {
        let mut e = tmp_engine("work");
        e.state.coins = 100;
        // 测试里先让鲸鱼娘不参与移动,避免随机占位影响这条断言的稳定性
        if let Some(j) = e.state.characters.iter_mut().find(|c| c.id == "jingyu") {
            j.hired = false;
        }
        assert_eq!(e.action(&json!({"type":"hire","id":"nini"}))["ok"], true);
        e.state.plots[0].watered_at = None;
        assert_eq!(e.action(&json!({"type":"assign_work","id":"nini","job":"water"}))["ok"], true);
        let start = now_ts();
        let start_pos = {
            let c = e.state.characters.iter().find(|c| c.id == "nini").unwrap();
            (c.x, c.y, c.floor)
        };
        for k in 0..40 {
            e.tick_at(start + k);
        }
        let nini = e.state.characters.iter().find(|c| c.id == "nini").unwrap();
        assert!(
            (nini.x, nini.y, nini.floor) != start_pos,
            "角色必须真的移动,不能瞬移/不动"
        );
        assert!(
            e.state.plots[0].watered_at.is_some(),
            "浇水完成后地块状态必须真实变化"
        );
        assert!(
            e.state.events.iter().any(|ev| ev.kind == "work_done"),
            "要写完成事件"
        );
        assert!(!e.state.work_history.is_empty(), "要有工作历史");
        assert!(!e.state.memory_queue.is_empty(), "真实事件要进记忆同步队列");
    }

    #[test]
    fn furniture_has_its_own_detail_card_data() {
        let mut e = tmp_engine("detail");
        let snap = e.snapshot();
        let furn = snap["furniture"].as_array().unwrap();
        assert!(furn.len() >= 20);
        let mut descs = std::collections::BTreeSet::new();
        for f in furn {
            let desc = f["desc"].as_str().unwrap_or("");
            let ty = f["type"].as_str().unwrap_or("");
            assert!(!desc.is_empty(), "每件家具都要有说明");
            assert!(!ty.is_empty(), "每件家具都要有类型");
            descs.insert(desc.to_string());
        }
        assert!(descs.len() >= 12, "不同家具的详情不能全是同一句");
        // 床和书架说明必须不同
        let bed = furn.iter().find(|f| f["kind"] == "bed").unwrap();
        let shelf = furn.iter().find(|f| f["kind"] == "shelf").unwrap();
        assert_ne!(bed["desc"], shelf["desc"]);
    }

    #[test]
    fn life_status_never_leaks_hire_state() {
        let mut e = tmp_engine("memory");
        let c = e.state.characters.iter().find(|c| c.id == "nini").unwrap().clone();
        let text = e.life_state_text(&c);
        assert!(!text.contains("雇佣"), "生活状态不能带雇佣信息: {}", text);
        assert!(!text.contains("未雇佣"));
        // 当前状态同步走 home:state:<id>,正文同样干净
        e.queue_character_state(1);
        let last = e.state.memory_queue.last().unwrap();
        let content = last["body"]["content"].as_str().unwrap_or("");
        let ctx = last["body"]["context"].to_string();
        assert!(!content.contains("雇佣"), "记忆正文不能带雇佣信息");
        assert!(!ctx.contains("hired"), "context 也不能带 hired 字段");
        assert!(last["body"]["source_ref"].as_str().unwrap_or("").starts_with("home:state:"));
        // 历史事件:只写值得记的(收获/买卖…),且 ref 唯一
        let ev = Event { at: fmt_ts(now_ts()), kind: "harvest".into(), text: "泥泥收了番茄".into(), who: "泥泥".into(), source: "farm".into(), ..Default::default() };
        e.push_event(ev.clone());
        e.push_event(Event { kind: "sell".into(), text: "泥泥卖了番茄".into(), ..ev });
        let refs: Vec<String> = e.state.memory_queue.iter().filter_map(|q| q["body"]["source_ref"].as_str().map(|s| s.to_string())).collect();
        let event_refs: Vec<&String> = refs.iter().filter(|r| r.starts_with("home:event:")).collect();
        assert!(event_refs.len() >= 2, "重要的历史事件要入队");
        let uniq: std::collections::BTreeSet<&&String> = event_refs.iter().collect();
        assert_eq!(uniq.len(), event_refs.len(), "事件 source_ref 不能重复");
        // 普通事件(浇水)不进记忆库
        let before = e.state.memory_queue.len();
        e.push_event(Event { kind: "water".into(), text: "泥泥给番茄浇了水".into(), who: "泥泥".into(), ..Default::default() });
        assert_eq!(e.state.memory_queue.len(), before, "普通浇水只进本地日记");
        // 同角色同类事件 10 分钟内最多一条
        e.push_event(Event { kind: "harvest".into(), text: "薇薇收了胡萝卜".into(), who: "薇薇".into(), ..Default::default() });
        let after_one = e.state.memory_queue.len();
        e.push_event(Event { kind: "harvest".into(), text: "薇薇收了土豆".into(), who: "薇薇".into(), ..Default::default() });
        assert_eq!(e.state.memory_queue.len(), after_one, "同角色同类 10 分钟最多 1 条");
    }

    #[test]
    fn two_characters_never_occupy_same_cell() {
        let mut e = tmp_engine("collision");
        e.state.coins = 100;
        if let Some(j) = e.state.characters.iter_mut().find(|c| c.id == "jingyu") {
            j.hired = false;
        }
        assert_eq!(e.action(&json!({"type":"hire","id":"nini"}))["ok"], true);
        assert_eq!(e.action(&json!({"type":"hire","id":"huahua"}))["ok"], true);
        assert_eq!(e.action(&json!({"type":"assign_work","id":"nini","job":"tidy"}))["ok"], true);
        assert_eq!(e.action(&json!({"type":"assign_work","id":"huahua","job":"tidy"}))["ok"], true);
        let start = now_ts();
        for k in 0..160 {
            e.tick_at(start + k);
            let mut seen = std::collections::BTreeSet::new();
            for c in e.state.characters.iter().filter(|c| c.hired) {
                let key = (c.floor, c.x.floor() as i32, c.y.floor() as i32);
                assert!(
                    seen.insert(key),
                    "两个角色占了同一格 {:?}（{} 在 {}F {},{}）",
                    key, c.name, c.floor, c.x, c.y
                );
            }
        }
    }

    /// 给 UI 的完整 home_snapshot 样例
    #[test]
    fn print_snapshot_sample() {
        let mut e = tmp_engine("sample");
        e.state.coins = 100;
        let _ = e.action(&json!({"type":"hire","id":"nini"}));
        e.state.plots[0].watered_at = None;
        let _ = e.action(&json!({"type":"assign_work","id":"nini","job":"water"}));
        let now = now_ts();
        for k in 0..40 {
            e.tick_at(now + k);
        }
        println!("SNAPSHOT_BEGIN");
        println!("{}", serde_json::to_string_pretty(&e.snapshot()).unwrap());
        println!("SNAPSHOT_END");
    }

    #[test]
    fn care_bumps_three_stats() {
        let mut e = tmp_engine("care");
        let now = now_ts();
        {
            let c = e.state.characters.first_mut().unwrap();
            c.mood = 40;
            c.full = 40;
            c.clean = 40;
            c.energy = 90;
        }
        let r = e.do_care("bathe", now).unwrap();
        assert!(r["clean"].as_i64().unwrap() > 40, "洗澡应提升清洁");
        let r = e.do_care("eat", now).unwrap();
        assert!(r["full"].as_i64().unwrap() > 40, "吃饭应提升饱腹");
        let r = e.do_care("play", now).unwrap();
        assert!(r["mood"].as_i64().unwrap() > 40, "陪玩应提升心情");
        assert!(e.do_care("nope", now).is_err(), "未知照顾方式应报错");
    }

    #[test]
    fn gather_adds_items_and_has_cooldown() {
        let mut e = tmp_engine("gather");
        let now = now_ts();
        e.state.items.clear();
        e.state.last_gather_at.clear();
        e.state.characters.first_mut().unwrap().energy = 100;
        let r = e.do_gather("hill", now).unwrap();
        assert!(!r["got"].as_array().unwrap().is_empty(), "采集应有收获");
        let total: i64 = e.state.items.values().copied().sum();
        assert!(total >= 1, "物品柜应有东西");
        assert!(e.do_gather("hill", now + 30).is_err(), "冷却中应拒绝");
        e.state.characters.first_mut().unwrap().energy = 2;
        e.state.last_gather_at.clear();
        assert!(e.do_gather("sea", now + 2000).is_err(), "能量不足应拒绝");
    }

    #[test]
    fn craft_consumes_materials_then_sells() {
        let mut e = tmp_engine("craft");
        let now = now_ts();
        e.state.items.clear();
        e.state.items.insert("木头".into(), 2);
        assert!(e.do_craft("pearl_necklace", now).is_err(), "缺材料应报错");
        let r = e.do_craft("plank", now).unwrap();
        assert_eq!(r["item"].as_str().unwrap(), "木板");
        assert_eq!(e.state.items.get("木头").copied().unwrap_or(0), 0);
        let before = e.state.coins;
        let r = e.do_sell_item("plank", 1, now).unwrap();
        assert_eq!(r["gain"].as_i64().unwrap(), 3);
        assert!(e.state.coins > before);
        assert!(e.do_sell_item("plank", 1, now).is_err(), "卖光应报错");
    }

    #[test]
    fn snapshot_exposes_stats_items_and_ledger() {
        let mut e = tmp_engine("snapstats");
        let now = now_ts();
        e.state.items.clear();
        e.state.last_gather_at.clear();
        let _ = e.do_gather("sea", now);
        let s = e.snapshot();
        let c = &s["characters"][0];
        assert!(c["mood"].is_number() && c["full"].is_number() && c["clean"].is_number());
        assert!(s["items"].is_object(), "快照要有物品柜");
        assert!(s["ledger"].is_array(), "快照要有账本");
        assert!(s["item_prices"].is_object(), "快照要有物品价格");
        assert!(s["her"]["mood"].is_number(), "her 里也要有心情");
    }
}
