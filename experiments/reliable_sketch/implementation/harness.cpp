// JumpServe adapter: author sketch files remain byte-exact; RNG and inputs are controlled.
#include <sketch/rs.h>
#include <sketch/cm.h>
#include <sketch/cu.h>
#include <murmur3.h>
#include <utils.h>
#include <chrono>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <map>
#include <memory>
#include <random>
#include <cstdarg>
#include <cstdlib>
#include <cmath>
#include <functional>
static std::mt19937 rng;
uint32_t RandUint32() { return rng(); }
void panic(char *format, ...) { std::cerr << "Author sketch rejected configuration"; std::exit(2); }
long mass(ReliableSketch* s) {
 long v=0; for(int i=0;i<s->rs_level;i++)for(int j=0;j<s->rs_row_size[i];j++) v+=s->rs[i][j].yes_cnt+s->rs[i][j].no_cnt; return v;
}
long allocated(ReliableSketch* s) {return long(s->mf_num_bkt)*sizeof(int)+long(s->rs_num_bkt)*sizeof(ReliableSketch::Bucket);}
int main(int argc,char**argv) {
 if(argc!=8) return 2;
 std::string alg=argv[1],regime=argv[2],input=argv[6];
 int budget=std::stoi(argv[3]),seed=std::stoi(argv[4]),threshold=std::stoi(argv[5]);
 double nominal=budget,ratio=alg=="RS"?0.2:0.;
 rng.seed(seed);
 // The author base class has no virtual destructor; delete each concrete type.
 std::unique_ptr<Sketch,std::function<void(Sketch*)>> sketch(nullptr,[alg](Sketch* ptr){if(alg=="RS"||alg=="RS_raw")delete static_cast<ReliableSketch*>(ptr);else if(alg=="CM3"||alg=="CM16")delete static_cast<CMSketch*>(ptr);else delete static_cast<CUSketch*>(ptr);}); ReliableSketch* rs=nullptr;
 if(alg=="RS"||alg=="RS_raw") {
  if(regime=="allocated") nominal=std::floor(budget/(ratio*16+(1-ratio)*1.6));
  rs=new ReliableSketch(nominal,ratio,20,threshold-(ratio?3:0),2.,2.5,3,2);sketch.reset(rs);
 } else if(alg=="CM3"||alg=="CM16") sketch.reset(new CMSketch(nominal,alg=="CM3"?3:16));
 else if(alg=="CU3"||alg=="CU16") sketch.reset(new CUSketch(nominal,alg=="CU3"?3:16));
 else return 2;
 sketch->init();
 if(std::string(argv[7])=="weighted") {
  int key=std::stoi(input);rs->insert(key,5);
  std::cout<<"{\"key\":"<<key<<",\"weight\":5,\"estimate\":"<<rs->query_freq(key)<<",\"lower\":"<<rs->query_freq_low(key)<<",\"bucket_mass\":"<<mass(rs)<<",\"filter_first\":"<<rs->mf[MurmurHash3_x86_32(&key,4,rs->rand_mf[0])%rs->mf_row_size]<<"}\n";return 0;
 }
 std::ifstream stream(input,std::ios::binary);if(!stream)return 3;
 std::vector<int> keys;std::map<int,long> truth;int key;
 while(stream.read(reinterpret_cast<char*>(&key),4)){keys.push_back(key);truth[key]++;}
 if(keys.empty()||!stream.eof())return 4;
 long filtered=0;
 if(rs&&rs->mf_num_bkt) {
  // Diagnostics outside timed path. Replay to count updates consumed by the filter.
  for(int v:keys){int m=2147483647;for(int j=0;j<rs->mf_n_hash;j++){int p=MurmurHash3_x86_32(&v,4,rs->rand_mf[j])%rs->mf_row_size+rs->mf_row_size*j;m=std::min(m,rs->mf[p]);}filtered+=(m<rs->mf_err_bound);rs->insert(v);}rs->init();
 }
 auto begin=std::chrono::steady_clock::now();for(int v:keys)sketch->insert(v);
 double insert_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count();
 long checksum=0;begin=std::chrono::steady_clock::now();for(int v:keys)checksum+=sketch->query_freq(v);
 double query_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count();
 long actual=rs?allocated(rs):long(nominal/4)*4;
 if(regime=="allocated"&&actual>budget)return 5;
 std::cout<<std::setprecision(12)<<"{\"algorithm\":\""<<alg<<"\",\"budget_regime\":\""<<regime<<"\",\"requested_bytes\":"<<budget<<",\"nominal_bytes\":"<<nominal<<",\"allocated_counter_bytes\":"<<actual<<",\"bucket_size\":"<<sizeof(ReliableSketch::Bucket)<<",\"seed\":"<<seed<<",\"hash_base\":";
 std::mt19937 seed_check(seed);std::cout<<(seed_check()%13337);
 std::cout<<",\"updates\":"<<keys.size()<<",\"distinct_keys\":"<<truth.size()<<",\"insert_seconds\":"<<insert_seconds<<",\"query_seconds\":"<<query_seconds<<",\"checksum\":"<<checksum<<",\"filtered_updates\":"<<filtered<<",\"bucket_mass\":";
 if(rs)std::cout<<mass(rs);else std::cout<<"null";
 std::cout<<",\"layers\":"<<(rs?rs->rs_level:0)<<",\"threshold_sum\":"<<(rs?rs->rs_eps+(ratio?3:0):0)<<",\"query_population\":\"observed keys plus three absent probes\"}\nkey,truth,estimate,lower\n";
 truth[0]=0;truth[-1]=0;truth[1000001]=0;
 for(auto const& entry:truth){std::cout<<entry.first<<","<<entry.second<<","<<sketch->query_freq(entry.first)<<",";if(rs)std::cout<<rs->query_freq_low(entry.first);std::cout<<"\n";}
}
