process.env.COPILOTKIT_TELEMETRY_DISABLED='true';
process.env.DO_NOT_TRACK='1';
const original=globalThis.fetch;
globalThis.fetch=(input,options)=>{
  const url=new URL(input instanceof Request?input.url:input.toString());
  if(url.hostname!=='127.0.0.1')throw new Error('unit tests forbid real business/model requests');
  return original(input,options);
};
