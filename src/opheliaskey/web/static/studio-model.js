// Lyric Show growth model — the JavaScript mirror of analysis/studio.py's funnel, reach and
// target arithmetic. Shared by /studio (growth story) and /studio/full (the working), and inlined
// into the standalone export. tests/test_studio_parity.py runs the @@mirror block in node against
// the Python model, so keep the formulas identical to studio.py.
(function (root) {
  'use strict';
  // @@mirror-start (tests/test_studio_parity.py runs this block in node against the Python model)
  function computeFunnel(rep, inputs) {
    const a = k => rep.assumptions[k].value;
    // a slider value when the reader moved one, else the report's own (possibly observed) input
    const v = k => (k in inputs ? inputs[k] : rep.funnel.inputs[k].value);
    const commission = a('store_commission'), horizon = a('horizon_months');
    const plans = rep.funnel.by_plan;
    const viewersStream = v('viewers_per_show') * v('shows_per_month');
    const viewersEvent = v('viewers_per_show') * v('event_viewers_multiplier') * v('events_per_month');
    // partner streams: data rows; prospective partners count only when the switch is on
    const includeHyp = (v('partners_include_hypothetical') || 0) >= 0.5, liveShare = v('partner_live_share');
    const partnerRows = ((rep.reach && rep.reach.partners) || []).map(p => {
      const active = p.status === 'committed' || includeHyp;
      // a partner's audience is an assumption too (partner_<key>_subscribers / _live_viewers): a lever when present
      const subsKey = 'partner_' + p.key + '_subscribers', liveKey = 'partner_' + p.key + '_live_viewers';
      const subs = (subsKey in inputs || (rep.funnel.inputs && rep.funnel.inputs[subsKey])) ? v(subsKey) : (p.subscribers || 0);
      const live = (liveKey in inputs || (rep.funnel.inputs && rep.funnel.inputs[liveKey])) ? v(liveKey) : (p.live_viewers_per_stream || 0);
      const perStream = p.kind === 'artist' ? subs * liveShare : live;
      const vm = perStream * p.streams_per_month;
      return { ...p, active, subscribers: p.kind === 'artist' ? Math.round(subs) : p.subscribers, live_viewers_per_stream: p.kind === 'artist' ? p.live_viewers_per_stream : Math.round(live),
        viewers_per_month: +vm.toFixed(2), viewers_abroad: +(vm * p.international_share).toFixed(2), counted: active ? vm : 0 };
    });
    const viewersPartner = partnerRows.reduce((s, p) => s + p.counted, 0);
    const viewers = viewersStream + viewersEvent + viewersPartner;
    const attendees = v('dock_attendees_per_event') * v('events_per_month');
    // Two audiences by need. Travelers (Conversation Mode) are the primary buyer; the dock crowd are travelers.
    const tShare = v('traveler_share');
    const travelersViewers = viewers * tShare, performersViewers = viewers - travelersViewers;
    const installsEvent = attendees * v('attendee_to_install');
    const installsStream = travelersViewers * v('traveler_viewer_to_install') + performersViewers * v('viewer_to_install');
    const installsTravelers = travelersViewers * v('traveler_viewer_to_install') + installsEvent;
    const installsPerformers = performersViewers * v('viewer_to_install');
    const installs = installsTravelers + installsPerformers;
    const newPaidT = installsTravelers * v('traveler_install_to_paid'), newPaidP = installsPerformers * v('install_to_paid');
    const newPaid = newPaidT + newPaidP;
    // plan prices arrive already blended for annual billing (the annual shares are not sliders)
    const tPlan = plans.find(p => p.segment === 'traveler'), pPlans = plans.filter(p => p.segment !== 'traveler');
    const arpuT = tPlan ? tPlan.price_cents : 0;
    const arpuP = pPlans.reduce((s, p) => s + (p.mix_share != null ? p.mix_share : p.share) * p.price_cents, 0);
    const arpu = newPaid > 0 ? (newPaidT * arpuT + newPaidP * arpuP) / newPaid : arpuP;
    const churnP = v('monthly_churn'), churnT = v('traveler_monthly_churn'), keep = 1 - commission;
    const baseline = (rep.funnel.inputs.baseline_subscribers || { value: 0 }).value || 0;
    const steadyT = newPaidT / churnT, steadyP = newPaidP / churnP, steady = steadyT + steadyP;
    const traj = []; let subsT = 0, subsP = 0, base = baseline, cum = 0, cumShow = 0;
    for (let t = 1; t <= horizon; t++) {
      subsT = subsT * (1 - churnT) + newPaidT; subsP = subsP * (1 - churnP) + newPaidP; base = base * (1 - churnP);
      const show = (subsT * arpuT + subsP * arpuP) * keep, mrr = show + base * arpu * keep; cum += mrr; cumShow += show;
      const subs = subsT + subsP + base;
      traj.push({ month: t, subscribers: +subs.toFixed(1), subscribers_travelers: +subsT.toFixed(1), subscribers_performers: +subsP.toFixed(1),
        baseline_subscribers: +base.toFixed(1), mrr_net_cents: Math.round(mrr), cumulative_net_cents: Math.round(cum),
        show_driven_subscribers: +(subsT + subsP).toFixed(1), show_driven_mrr_net_cents: Math.round(show), show_driven_cumulative_net_cents: Math.round(cumShow) }); }
    const first = pred => { const r = traj.find(pred); return r ? r.month : null; };
    const kit = rep.kit.planned_cents, proj = rep.breakeven.project_spend_cents, slip = rep.breakeven.moorage_monthly_cents;
    const m12 = traj.length >= 12 ? traj[11] : null, last = traj[traj.length - 1];
    const cpi = rep.lenses.acquisition_displaced.cpi_cents;
    const steadyNetT = steadyT * arpuT * keep, steadyNetP = steadyP * arpuP * keep, steadyNet = steadyNetT + steadyNetP;
    const showsPerMonth = v('shows_per_month') + v('events_per_month');
    const breakeven = { kit_month: first(r => r.show_driven_cumulative_net_cents >= kit), project_month: proj > 0 ? first(r => r.show_driven_cumulative_net_cents >= proj) : null,
      slip_month: slip == null ? null : first(r => r.show_driven_mrr_net_cents >= slip) };
    const ratio = (a, b) => (b > 0 ? +(a / b).toFixed(2) : null);
    const yieldPerViewer = tShare * v('traveler_viewer_to_install') * v('traveler_install_to_paid') + (1 - tShare) * v('viewer_to_install') * v('install_to_paid');
    partnerRows.forEach(p => { p.new_paid_per_month = +(p.counted * yieldPerViewer).toFixed(2); });
    const tgt = v('target_subscribers') || 0, tgtM = Math.round(v('target_month') || 0);
    const atM = tgtM >= 1 && tgtM <= traj.length ? traj[tgtM - 1].subscribers : null;
    const reachedMonth = tgt > 0 ? first(r => r.subscribers >= tgt) : null;
    let geo = 0; for (let i = 0; i < tgtM; i++) geo += Math.pow(1 - churnT, i);
    const reqNew = tgt > 0 && geo > 0 ? tgt / geo : null;
    const reqViewers = reqNew != null && yieldPerViewer > 0 ? reqNew / yieldPerViewer : null;
    const target = { subscribers: tgt, month: tgtM, subscribers_at_target_month: atM, reached_month: reachedMonth,
      shortfall: atM == null || tgt <= 0 ? null : +Math.max(0, tgt - atM).toFixed(1),
      required_new_paid_per_month: reqNew == null ? null : +reqNew.toFixed(2), required_viewers_per_month: reqViewers == null ? null : +reqViewers.toFixed(2),
      on_track: tgt > 0 ? (reachedMonth != null && reachedMonth <= tgtM) : null, note: rep.target ? rep.target.note : '' };
    const reach = { ...(rep.reach || {}), partners: partnerRows, viewers_partner_per_month: +viewersPartner.toFixed(2),
      viewers_abroad_per_month: +partnerRows.reduce((s, p) => s + (p.active ? p.viewers_abroad : 0), 0).toFixed(2),
      viewers_total_per_month: +viewers.toFixed(2) };
    const roi = { kit_cents: kit,
      month_12: m12 ? { show_driven_cumulative_net_cents: m12.show_driven_cumulative_net_cents, roi_multiple_on_kit: ratio(m12.show_driven_cumulative_net_cents, kit) } : null,
      horizon: { show_driven_cumulative_net_cents: last.show_driven_cumulative_net_cents, roi_multiple_on_kit: ratio(last.show_driven_cumulative_net_cents, kit), share_of_project_spend: ratio(last.show_driven_cumulative_net_cents, proj) },
      per_show_net_cents: showsPerMonth > 0 ? Math.round(steadyNet / showsPerMonth) : null,
      per_viewer_net_cents: viewers > 0 ? Math.round(steadyNet / viewers) : null,
      cost_per_install_cents: installs > 0 ? Math.round(kit / (installs * 12)) : null,
      payback: breakeven, note: rep.roi ? rep.roi.note : '' };
    return {
      reach, target,
      funnel: { monthly: { viewers: +viewers.toFixed(2), viewers_stream: +viewersStream.toFixed(2), viewers_event: +viewersEvent.toFixed(2), viewers_partner: +viewersPartner.toFixed(2),
          attendees: +attendees.toFixed(2), installs: +installs.toFixed(2), installs_stream: +installsStream.toFixed(2),
          installs_event: +installsEvent.toFixed(2), new_paid: +newPaid.toFixed(2),
          travelers_viewers: +travelersViewers.toFixed(2), performers_viewers: +performersViewers.toFixed(2),
          installs_travelers: +installsTravelers.toFixed(2), installs_performers: +installsPerformers.toFixed(2),
          new_paid_travelers: +newPaidT.toFixed(2), new_paid_performers: +newPaidP.toFixed(2) },
        by_plan: plans.map(p => { const n = p.segment === 'traveler' ? newPaidT : newPaidP * (p.mix_share != null ? p.mix_share : p.share);
          return { ...p, new_subscribers: +n.toFixed(2), share: newPaid > 0 ? +(n / newPaid).toFixed(3) : 0 }; }),
        arpu_gross_cents: Math.round(arpu), arpu_by_segment: { travelers: Math.round(arpuT), performers: Math.round(arpuP) },
        steady_state: { subscribers: +steady.toFixed(1), subscribers_travelers: +steadyT.toFixed(1), subscribers_performers: +steadyP.toFixed(1),
          mrr_gross_cents: Math.round(steadyT * arpuT + steadyP * arpuP), mrr_net_cents: Math.round(steadyNet), arr_net_cents: Math.round(12 * steadyNet),
          mrr_net_travelers_cents: Math.round(steadyNetT), mrr_net_performers_cents: Math.round(steadyNetP) },
        trajectory: traj },
      roi,
      lenses: { subscription: { steady_subscribers: +steady.toFixed(1), steady_mrr_net_cents: Math.round(steadyNet),
          steady_arr_net_cents: Math.round(12 * steadyNet), month_12: m12,
          by_segment: { travelers: { steady_subscribers: +steadyT.toFixed(1), steady_mrr_net_cents: Math.round(steadyNetT) },
                        performers: { steady_subscribers: +steadyP.toFixed(1), steady_mrr_net_cents: Math.round(steadyNetP) } } },
        acquisition_displaced: { installs_per_month: +installs.toFixed(2), cpi_cents: cpi, monthly_cents: Math.round(installs * cpi), annual_cents: Math.round(12 * installs * cpi) },
        catalog: { ...rep.lenses.catalog, songs_per_month: +(a('songs_per_show') * v('shows_per_month')).toFixed(1), songs_per_year: +(a('songs_per_show') * v('shows_per_month') * 12).toFixed(1),
          buskers_per_month: +(a('buskers_per_event') * v('events_per_month')).toFixed(1), buskers_per_year: +(a('buskers_per_event') * v('events_per_month') * 12).toFixed(1) } },
      breakeven,
    };
  }
  // @@mirror-end
  root.StudioModel = { computeFunnel };
})(typeof window !== 'undefined' ? window : globalThis);
