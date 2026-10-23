import { useState } from "react";
import "./landing.css";
import LiveChat from "./components/LiveChat";

export default function Landing() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="site-shell">
      <header className="site-header">
        <a className="brand" href="#overview"><span className="brand-symbol">S</span><span>ShopMind <b>AI</b></span></a>
        <button className="menu-toggle" type="button" onClick={() => setMenuOpen((open) => !open)} aria-label="Open navigation"><span /><span /></button>
        <nav className={menuOpen ? "site-nav open" : "site-nav"}>
          {[["Overview", "overview"], ["Dual Modes", "modes"], ["Decision Flow", "orchestration"], ["Demo", "demo"], ["Product", "live"]].map(([item, anchor]) => <a key={item} href={`#${anchor}`} onClick={() => setMenuOpen(false)}>{item}</a>)}
        </nav>
        <div className="header-actions"><a className="case-link" href="#demo">View Case Study <span>↗</span></a><a className="primary-btn compact" href="#live">真实对话 <span>↗</span></a></div>
      </header>

      <main>
        <section className="hero" id="overview">
          <div className="hero-copy reveal">
            <p className="eyebrow">CONVERSATIONAL COMMERCE / 01</p>
            <h1>ShopMind AI</h1>
            <h2 className="hero-subtitle">大概知道买什么，却怕漏了关键？</h2>
            <p className="hero-text">ShopMind 把生活任务拆成完整采购方案，核对已有与缺项，再按预算比较候选，讲清该买、可不买和每项取舍。</p>
            <div className="hero-actions"><a className="primary-btn" href="#live">开始购物对话 <span>↗</span></a><a className="secondary-btn" href="#orchestration">查看能力 <span>↓</span></a></div>
            <div className="capability-tags"><span>Intent Understanding</span><span>Tool Calling</span><span>Explainable Recommendations</span></div>
          </div>
          <div className="hero-console reveal delayed" aria-label="ShopMind 从生活任务到采购方案的流程动画">
            <div className="console-header"><div>ShopMind · 决策预览</div><span>模拟商品 · 不实际下单</span></div>
            <p className="preview-slogan">列清单我来，标记已有 / 要买你来；商品只为缺的推荐。</p>
            <div className="preview-journey">
              <div className="preview-query">
                <small>01 · 你的需求</small>
                <p>周末去海边两天，一个人，预算 300 元，想轻便，帮我准备清单。</p>
              </div>
              <div className="preview-reply">
                <small>02 · ShopMind 整理清单</small>
                <p>按场景拆出 16 项，分组逐项确认；没标记的不会默认已有。下方预览 2 项。</p>
              </div>
              <section className="preview-checklist" aria-label="海边清单状态预览">
                <header><b>游泳与海边活动</b><span>分组清单 · 2 / 16 项</span></header>
                <article className="preview-item required">
                  <div className="preview-item-meta"><b>泳衣 <em>必备</em></b><small>1–2 套 · 按行程和晾晒条件调整</small></div>
                  <div className="preview-status" role="group" aria-label="泳衣状态示意">
                    <span>已有</span><span className="selected required">✓ 需要购买</span><span>还没确认</span>
                  </div>
                </article>
                <article className="preview-item optional">
                  <div className="preview-item-meta"><b>泳镜 <em>按需</em></b><small>计划游泳时</small></div>
                  <div className="preview-status" role="group" aria-label="泳镜状态示意">
                    <span className="selected optional">✓ 已有</span><span>这次要买</span><span>不需要</span><span>还不确定</span>
                  </div>
                </article>
              </section>
              <section className="preview-candidate" aria-label="只为步骤 02 中标记待购的泳衣推荐模拟商品">
                <div className="preview-candidate-head"><small>03 · 商品候选</small><span>对应步骤 02：泳衣 · 需要购买</span></div>
                <div className="preview-products">
                  <article className="preview-product-card featured">
                    <small>轻便首选</small><b>SwimEase<br />轻便速干泳衣</b>
                    <p>匹配“轻便”；用于海边游泳</p><strong>CNY 159</strong>
                  </article>
                  <article className="preview-product-card">
                    <small>轻薄连体</small><b>WaveDry<br />轻薄速干连体泳衣</b>
                    <p>连体款式；购买前核对尺码</p><strong>CNY 189</strong>
                  </article>
                  <article className="preview-product-card">
                    <small>耐用定位</small><b>ShopMind模拟<br />泳衣耐用升级款</b>
                    <p>演示目录款；实际参数待核实</p><strong>CNY 129</strong>
                  </article>
                </div>
              </section>
              <div className="preview-confirm">
                <div><small>04 · 方案确认</small><b>待确认候选 CNY 159 · 预算 CNY 300</b></div>
                <span>方案待确认</span>
                <p>演示到采购方案确认；真实购物车、订单与支付尚未接入。</p>
              </div>
            </div>
            <div className="console-glow" />
          </div>
        </section>

        <section className="value-section">
          <div className="section-heading"><p className="eyebrow">WHY SHOPMIND</p><h2>你缺的不是商品，<span>而是做决定的把握。</span></h2></div>
          <div className="value-grid">
            {[["01", "⌁", "怕漏买，也怕买多", "面对一次旅行、入住或送礼任务，用户往往不是找不到商品，而是不确定到底要准备哪些、哪些已有、哪些其实不用买。"], ["02", "⌘", "目标不是多推荐，而是刚好完成", "平台可以继续给出更多单品；ShopMind 先控制采购范围，优先生成完成任务所需的最小购买集合。"], ["03", "✦", "把选择依据交还给用户", "每个候选都说明为什么选、放弃了什么、还缺什么信息；用户调整预算或优先级后，方案跟着变化。"]].map(([index, icon, title, text]) => (
              <article className="value-card" key={title}><span className="value-index">{index}</span><div className="value-icon">{icon}</div><h3>{title}</h3><p>{text}</p></article>
            ))}
          </div>
        </section>

        <section className="modes-section" id="modes">
          <div className="section-heading split-heading"><div><p className="eyebrow">DUAL MODES</p><h2>同一套能力，<span>两种进入方式。</span></h2></div><p>知道商品词时直接搜索；只知道生活目标时，先规划任务再形成采购方案。</p></div>
          <div className="mode-grid">
            <article className="mode-card">
              <div className="mode-top"><span>01 / SEARCH</span><b>关键词搜索模式</b></div>
              <div className="search-input-demo"><span>⌕</span><p>防水 蓝牙耳机 200-300</p><i>↗</i></div>
              <div className="filter-chips"><span>防水</span><span>蓝牙</span><span>200-300</span></div>
              <p className="mode-copy">快、可控、适合已经知道商品词和筛选条件的用户。</p><div className="mode-line" /><strong>Structured Query → Search → Filter</strong>
            </article>
            <article className="mode-card">
              <div className="mode-top"><span>02 / CONVERSATION</span><b>问答对话模式</b></div>
              <div className="mode-dialog user">第一次租房入住，房间提供情况还不确定。</div><div className="mode-dialog ai">先按睡眠照明、入住清洁、收纳日常分组；未知项保持“还不确定”，不默认已有。</div>
              <p className="mode-copy chat-copy">必备与按需分开，逐项确认状态；只为明确待购项检索商品。</p><div className="mode-line" /><strong>Scene → Checklist → Confirm → Search</strong>
            </article>
          </div>
        </section>

        <section className="orchestration-section" id="orchestration">
          <div className="section-heading"><p className="eyebrow">DECISION FLOW</p><h2>把一次购买，<span>收敛成一套可确认的方案。</span></h2></div>
          <div className="flow-scroll"><div className="flow">
            {[["01", "场景澄清", "确认人数、时间、空间或特殊活动等边界"], ["02", "清单确认", "先列完整品类，再拆出已有、待补缺口和按需项"], ["03", "收集用户判断", "用户补充已有物品、预算和优先关注点"], ["04", "匹配待补候选", "只为缺少的任务槽位生成商品卡片，不重复推荐已有物品"], ["05", "取舍比较", "按预算和优先级比较候选，说明选择与放弃的代价"], ["06", "确认或调整", "用户确认方案；若有变化，重新计算清单、候选和总价"]].map(([number, title, text], index, items) => (
              <div className="flow-step" key={title}><div className="flow-node" tabIndex={0}><span>{number}</span><b>{title}</b><p>{text}</p></div>{index < items.length - 1 && <div className="flow-connector"><i /></div>}</div>
            ))}
          </div></div>
          <p className="orchestration-note">反馈可以让选择回到追问或检索环节；用户没说的已有物品保持待确认。当前比较基于模拟商品目录，不提供全网实时比价，也不执行真实交易。</p>
        </section>

        <LiveChat />
      </main>

      <footer className="site-footer"><a className="brand" href="#overview"><span className="brand-symbol">S</span><span>ShopMind <b>AI</b></span></a><p>场景演示调用本地 Commerce API；场景清单优先使用模板，商品与价格来自模拟目录，不执行真实交易。</p><div><a href="https://github.com/Pyper000/Pyper000.github.io">GitHub</a><a href="#overview">Portfolio</a></div></footer>
    </div>
  );
}
