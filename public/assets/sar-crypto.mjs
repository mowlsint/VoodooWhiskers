// AES-256-GCM envelope; kept in sync with scripts/sar_crypto.py.
const ITERATIONS=600000,MAX_BYTES=16*1024*1024;
const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true});
export function validatePassword(password){
  if(typeof password!=="string"||password.length<12||password.length>128||!/^[\x20-\x7e]+$/.test(password))throw new Error("Use a password with at least twelve characters.");
}
function encode(bytes){let out="";for(let i=0;i<bytes.length;i+=8192)out+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(out);}
function decode(value,max){if(typeof value!=="string"||value.length>max*1.4+8)throw new Error("Invalid encrypted file.");const raw=atob(value);return Uint8Array.from(raw,c=>c.charCodeAt(0));}
function aad(purpose){if(!['request','workspace'].includes(purpose))throw new Error("Invalid file type.");return encoder.encode(`MOwlSINT-SAR-v1:${purpose}`);}
async function key(password,salt){
  validatePassword(password);
  const seed=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveKey']);
  return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:ITERATIONS,hash:'SHA-256'},seed,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
}
export async function encryptObject(payload,password,purpose){
  const data=encoder.encode(JSON.stringify(payload));if(data.length>MAX_BYTES)throw new Error("The file is too large.");
  const salt=crypto.getRandomValues(new Uint8Array(16)),nonce=crypto.getRandomValues(new Uint8Array(12));
  const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce,additionalData:aad(purpose),tagLength:128},await key(password,salt),data);
  return {v:1,purpose,kdf:'PBKDF2-SHA256',iterations:ITERATIONS,salt:encode(salt),nonce:encode(nonce),ciphertext:encode(new Uint8Array(ciphertext))};
}
export async function decryptObject(envelope,password,purpose){
  if(!envelope||envelope.v!==1||envelope.purpose!==purpose||envelope.kdf!=='PBKDF2-SHA256'||envelope.iterations!==ITERATIONS)throw new Error("Unknown encryption format.");
  const salt=decode(envelope.salt,16),nonce=decode(envelope.nonce,12),ciphertext=decode(envelope.ciphertext,MAX_BYTES+16);
  if(salt.length!==16||nonce.length!==12||ciphertext.length<16||ciphertext.length>MAX_BYTES+16)throw new Error("Invalid encrypted file.");
  const result=await crypto.subtle.decrypt({name:'AES-GCM',iv:nonce,additionalData:aad(purpose),tagLength:128},await key(password,salt),ciphertext);
  return JSON.parse(decoder.decode(result));
}
