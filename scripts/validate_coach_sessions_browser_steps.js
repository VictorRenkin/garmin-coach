async page => {
  const results = [];
  const output = 'reports/coach-v2-browser/';
  const control = action => page.evaluate(async action => {
    const response = await fetch('/__fixture__/' + action, {method:'POST'});
    if (!response.ok) throw Error('Synthetic fixture control failed');
    return response.json();
  }, action);
  const snapshot = async () => JSON.stringify((await control('snapshot')).snapshot);
  const calendar = () => page.evaluate(async () => (await (await fetch('/api/plan-proposal')).json()).proposal.sessions);
  const reset = async () => {
    await control('reset'); await page.reload();
    await page.getByRole('link',{name:'Coach',exact:true}).click();
    await page.locator('#chatInput').waitFor();
  };
  const send = async content => {
    await page.locator('#chatInput').fill(content);
    await page.locator('#chatSend').click();
  };
  const pending = () => page.getByRole('button',{name:'Confirmer',exact:true}).waitFor({timeout:20000});
  const shot = filename => page.screenshot({path:output+filename,fullPage:true});
  const confirmed = () => page.getByText('Modification locale confirmée. La séance révisée demande une nouvelle validation. Aucun envoi Garmin.',{exact:true}).waitFor();

  await reset();
  const beforeCancel = await snapshot();
  await send('Raccourcis ma séance du matin à 25 minutes.'); await pending();
  await page.getByRole('heading',{name:'Actuellement',exact:true}).waitFor();
  await page.getByRole('heading',{name:'Après confirmation',exact:true}).waitFor();
  await shot('01-preview-full.png');
  await page.getByRole('button',{name:'Annuler',exact:true}).click();
  await page.getByText('Brouillon annulé. Ce brouillon n’a pas modifié le plan.',{exact:true}).waitFor();
  if(await snapshot()!==beforeCancel)throw Error('Cancellation changed canonical files');
  results.push({case:'cancel',passed:true,canonicalUnchanged:true});
  await shot('02-cancelled-full.png');

  await send('Raccourcis ma séance du matin à 25 minutes.'); await pending();
  await page.getByRole('button',{name:'Confirmer',exact:true}).click(); await confirmed();
  let sessions = await calendar();
  if(sessions[0].duration_minutes!==25||sessions[0].structure.effort_minutes!==15||sessions[1].duration_minutes!==35)throw Error('Wrong duration/blocks/sibling');
  results.push({case:'confirm_duration',passed:true,seconds:1500,siblingMinutes:35});
  await shot('03-confirmed-full.png');
  await page.getByRole('link',{name:'Ma semaine',exact:true}).click();
  await page.locator('#planProposalGrid').getByText('25 min',{exact:true}).waitFor();
  await shot('04-calendar-25-full.png');

  await reset();await send('Raccourcis ma séance du matin à 25 minutes.');await pending();
  await control('stale');const beforeStale=await snapshot();
  await page.getByRole('button',{name:'Confirmer',exact:true}).click();
  await page.getByText("L'aperçu est devenu obsolète ; prépare une nouvelle demande.",{exact:true}).waitFor();
  if(await snapshot()!==beforeStale)throw Error('Stale confirmation changed canonical files');
  results.push({case:'stale',passed:true,canonicalUnchanged:true});
  await shot('05-stale-blocked.png');

  await reset();await control('published');const beforePublished=await snapshot();
  await send('Raccourcis ma séance du matin à 25 minutes.');
  await page.getByText('Cette séance peut déjà exister sur Garmin ; modification locale bloquée.',{exact:true}).waitFor({timeout:20000});
  if(await page.getByRole('button',{name:'Confirmer',exact:true}).count())throw Error('Published session offered confirmation');
  if(await snapshot()!==beforePublished)throw Error('Published session changed canonical files');
  results.push({case:'published',passed:true,canonicalUnchanged:true});
  await shot('06-published-blocked.png');

  await reset();await send("Change l'heure de la séance du soir.");await pending();
  await page.getByRole('button',{name:'Confirmer',exact:true}).click();await confirmed();
  sessions=await calendar();
  if(!sessions[1].starts_at.includes('20:00:00')||!sessions[0].starts_at.includes('08:00:00'))throw Error('Wrong evening identity/time');
  results.push({case:'evening_time',passed:true,morningUnchanged:true});
  await shot('07-evening-time.png');

  await reset();await send('Déplace la séance du soir à jeudi.');await pending();
  await page.getByRole('button',{name:'Confirmer',exact:true}).click();await confirmed();
  sessions=await calendar();
  if(sessions[1].date!=='2026-10-29'||sessions[0].date!=='2026-10-27')throw Error('Wrong moved identity/date');
  results.push({case:'move',passed:true,morningUnchanged:true});
  await shot('08-moved.png');

  await reset();await send('Rends la séance du matin plus facile.');await pending();
  await page.getByRole('button',{name:'Confirmer',exact:true}).click();await confirmed();
  sessions=await calendar();
  if(sessions[0].intensity_instruction!=='Très facile, sans rechercher la vitesse.'||sessions[1].intensity_instruction!=='Rester en aisance respiratoire.')throw Error('Wrong intensity/sibling');
  results.push({case:'easier_qualitative_instruction',passed:true,siblingUnchanged:true});
  await shot('09-easier.png');

  await reset();await send('Raccourcis ma séance du matin à 25 minutes.');await pending();
  const beforeNaturalCancel=await snapshot();
  await send('Annule, finalement.');
  await page.getByText("Brouillon annulé. Le plan n'a pas été modifié.",{exact:true}).waitFor({timeout:20000});
  if(await snapshot()!==beforeNaturalCancel)throw Error('Natural cancellation changed canonical files');
  if(await page.getByRole('button',{name:'Confirmer',exact:true}).count())throw Error('Cancelled draft still confirmable');
  results.push({case:'natural_cancel',passed:true,canonicalUnchanged:true});
  await shot('10-natural-cancel.png');
  return {syntheticOnly:true,realAI:false,results};
}
