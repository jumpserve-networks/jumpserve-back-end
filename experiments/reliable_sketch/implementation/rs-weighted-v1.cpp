#include <sketch/rs.h>
#include <murmur3.h>
#include <utils.h>

#include <cstdio>
#include <cstring>
#include <cmath>
#include <climits>
#include <cstdint>
#include <map>
using std::map;

// enable bit for id field
unsigned ID_MASK = 0xffffffff;
// minimal bucket num & threshold for MF
int MF_MIN_BKT = 16, MF_MIN_ERR = 1;
// minimal bucket num & threshold for RS
int RS_MIN_BKT = 1, RS_MIN_ERR = 0;
// memory consumption
int RS_BUCKET_SIZE = 10;

/*
Get RS required memory (Bytes) by level 0 bucket num
*/
double
ReliableSketch::get_rs_mem_by_bkt_0(int rs_bkt_0)
{
    int now_bkt = rs_bkt_0, now_err = rs_threshold_0;
    double sum_mem = 0.;
    for (int i = 0; i < rs_level && now_bkt >= RS_MIN_BKT && now_err >= RS_MIN_ERR; ++i) {
        sum_mem += (double)now_bkt * RS_BUCKET_SIZE;
        now_bkt = (int)ceil((double)now_bkt / rs_r_w);
        now_err = (int)floor((double)now_err / rs_r_l);
    }
    return sum_mem;
}

/*
Get RS level 0 bucket num by fixed memory amount (Bytes)
*/
int
ReliableSketch::get_rs_bkt_0_by_mem(double rs_mem)
{
    int L = 0, R = (int)(rs_mem / RS_BUCKET_SIZE), ans = L;
    while (L <= R) {
        int mid = (L + R) >> 1;
        double now_mem = get_rs_mem_by_bkt_0(mid);
        if (now_mem < rs_mem)
            ans = mid, L = mid+1;
        else
            R = mid-1;
    }
    return ans;
}

/*
Constructor of ReliableSketch

total_mem: total memory consumption of sketch
mem_ratio: MF mem / RS mem  (0 to disable MF)

rs_level: maximum level of RS
rs_threshold: threshold of RS
rs_r_w: memory decline rate of RS
rs_r_l: threshold decline rate of RS
mf_threshold: threshold of MF
*/
ReliableSketch::ReliableSketch(double total_mem, double mem_ratio,
                     int rs_level, int rs_threshold, double rs_r_w, double rs_r_l,
                     int mf_threshold, int mf_n_hash) :
total_mem(total_mem), mem_ratio(mem_ratio),
rs_level(rs_level), rs_r_w(rs_r_w), rs_r_l(rs_r_l),
mf_err_bound(mf_threshold), mf_n_hash(mf_n_hash)
{
	sprintf(name, "ReliableSketch");

    rand_base = RandUint32() % 13337;
    for (int i = 0; i < mf_n_hash; ++i)
        rand_mf[i] = rand_base + 100 + i;
    for (int i = 0; i < rs_level; ++i)
        rand_rs[i] = rand_base + i;

    rs_threshold_0 = ceil((double)rs_threshold/rs_r_l*(rs_r_l-1));
    double mf_mem = total_mem * mem_ratio;
    double rs_mem = total_mem - mf_mem;
    int mf_bkt_0 = (int)(mf_mem * 8 / (int)ceil(log2(mf_threshold)));
    int rs_bkt_0 = get_rs_bkt_0_by_mem(rs_mem);
    construct_mf(mf_bkt_0);
    construct_rs(rs_bkt_0);

    if (mem_ratio == 1.0)
        printf("[WARNING] No space for RS.");
    // else
    //     // strategy: never drop
    //     rs_err_bound[rs_level - 1] = 1e9;
}


/*
construct MF part

mf_bkt_0: level 0 bucket num of MF 
*/
void
ReliableSketch::construct_mf(int mf_bkt_0)
{
    int now_bkt = mf_bkt_0;
    mf_row_size = now_bkt / mf_n_hash;
    now_bkt = mf_row_size * mf_n_hash;  // alignment
    mf_num_bkt = now_bkt;
    if (mf_num_bkt == 0) // disable
    {
        mf = 0;
        return;
    }
    mf = new int[now_bkt];
}

/*
construct RS part

rs_bkt_0: level 0 bucket num of RS 
*/
void
ReliableSketch::construct_rs(int rs_bkt_0)
{
    int now_bkt = rs_bkt_0, now_err = rs_threshold_0, i;
    rs_num_bkt = rs_eps = 0;
    for (i = 0; i < rs_level && now_bkt >= RS_MIN_BKT && now_err >= RS_MIN_ERR; ++i) {
        rs[i] = new Bucket[now_bkt];
        rs_num_bkt += now_bkt;
        rs_eps += now_err;
        rs_row_size[i] = now_bkt;
        rs_err_bound[i] = now_err;

        now_bkt = (int)ceil((double)now_bkt / rs_r_w);
        now_err = (int)floor((double)now_err / rs_r_l);
    }
    rs_level = i;
}

ReliableSketch::~ReliableSketch()
{
    if (mf)
        delete [] mf;
	for (int i = 0; i < rs_level; ++i)
        if (rs[i])
		    delete [] rs[i];
}

void
ReliableSketch::init()
{
	memset(mf, 0, mf_num_bkt * sizeof(int));
	for (int i = 0; i < rs_level; ++i)
        memset(rs[i], 0, rs_row_size[i] * sizeof(Bucket));
    out_of_control = 0;
}

void
ReliableSketch::insert(int v)
{
    // MF
    if (mf_num_bkt)
    {
        int min_val = INT_MAX;
        for (register int j = 0; j < mf_n_hash; ++j) {
            int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
            int &bkt = mf[pos];
            min_val = min(min_val, bkt);
        }
        if (min_val != mf_err_bound) {
            for (register int j = 0; j < mf_n_hash; ++j) {
                int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
                int &bkt = mf[pos];
                if (bkt == min_val)
                    bkt = min(bkt+1, mf_err_bound);
            }
            return;
        }
    }

    int sv = (v & ID_MASK);
    for (register int i = 0; i < rs_level; ++i) {
		int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_rs[i]) % rs_row_size[i];
        Bucket &bkt = rs[i][pos];
        bool matched = bkt.id == sv;
        if (bkt.locked && !matched)
            continue;

        int est = (matched? bkt.yes_cnt : bkt.no_cnt);

        if (bkt.yes_cnt <= bkt.no_cnt)
        {
            bkt.id = sv;
            matched = true;
        }

        if (matched)
            bkt.yes_cnt++;
        else
        {
            bkt.no_cnt++;
            if (bkt.no_cnt >= rs_err_bound[i])
                bkt.locked = true;
        }
        break;
    }
}

void
ReliableSketch::insert(int v, int f)
{
    // MF
    if (mf_num_bkt) {
        int min_val = INT_MAX;
        for (register int j = 0; j < mf_n_hash; ++j) {
            int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
            int &bkt = mf[pos];
            min_val = min(min_val, bkt);
        }
        if (min_val != mf_err_bound) {
            int add_val = min(f, mf_err_bound - min_val);
            f -= add_val;
            for (register int j = 0; j < mf_n_hash; ++j) {
                int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
                int &bkt = mf[pos];
                bkt = max(bkt, min_val + add_val);
            }
            if (f == 0)
                return;
        }
    }

    int sv = (v & ID_MASK);
    for (register int i = 0; i < rs_level; ++i) {
		int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_rs[i]) % rs_row_size[i];
        Bucket &bkt = rs[i][pos];
        bool matched = bkt.id == sv;
        if (bkt.locked && !matched)
            continue;

        int est = (matched? bkt.yes_cnt : bkt.no_cnt);

        if (matched) {
            bkt.yes_cnt += f;
            f = 0;
        }
        else {
            int rem_occupy = bkt.yes_cnt - bkt.no_cnt;
            int rem_lock = rs_err_bound[i] - bkt.no_cnt;
            if (rem_occupy <= rem_lock) {
                if (f >= rem_occupy) {
                    bkt.no_cnt += rem_occupy;
                    bkt.yes_cnt += f - rem_occupy;
                    bkt.id = sv;
                    f = 0;
                }
                else {
                    bkt.no_cnt += f;
                    f = 0;
                }
            }
            else {
                if (f >= rem_lock) {
                    bkt.no_cnt += rem_lock;
                    f -= rem_lock;
                    bkt.locked = true;
                }
                else {
                    bkt.no_cnt += f;
                    f = 0;
                }
            }
        }

        if (f == 0)
            break;
    }
}

int
ReliableSketch::query_freq(int v)
{
    int sum = 0;
    // MF
    if (mf_num_bkt) {
        int min_val = INT_MAX;
        for (register int j = 0; j < mf_n_hash; ++j) {
            int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
            int bkt = mf[pos];
            min_val = min(min_val, bkt);
        }
        if (min_val < mf_err_bound)
            return min_val;
        sum += mf_err_bound;
    }

    int sv = (v & ID_MASK);
    for (register int i = 0; i < rs_level; ++i) {
		int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_rs[i]) % rs_row_size[i];
        Bucket &bkt = rs[i][pos];
        bool matched = bkt.id == sv;

        int est = (matched? bkt.yes_cnt : bkt.no_cnt);

        if (bkt.locked && !matched)
        {
            sum += est;
            continue;
        }

        // hit
        return sum + est;
    }
    return sum;
}

int
ReliableSketch::query_freq_low(int v)
{
    // MF
    if (mf_num_bkt) {
        int min_val = INT_MAX;
        for (register int j = 0; j < mf_n_hash; ++j) {
            int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
            int bkt = mf[pos];
            min_val = min(min_val, bkt);
        }
        if (min_val < mf_err_bound)
            return 0;
    }

    int sv = (v & ID_MASK);
    for (int i = 0; i < rs_level; ++i) {
		int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_rs[i]) % rs_row_size[i];
        Bucket &bkt = rs[i][pos];

        if (bkt.id != sv && bkt.locked)
        {
            continue;
        }

        if (bkt.id != sv)
        {
            return 0;
        }

        // hit
        return bkt.yes_cnt - bkt.no_cnt;
	}
	return 0;
}

int
ReliableSketch::query_freq_level(int v)
{
    if(mf_num_bkt) {
        int min_val = INT_MAX;
        for (register int j = 0; j < mf_n_hash; ++j) {
            int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
            int bkt = mf[pos];
            min_val = min(min_val, bkt);
        }
        if (min_val < mf_err_bound)
            return 99;  // filtered
    }

    int sv = (v & ID_MASK);
    for (int i = 0; i < rs_level; ++i) {
		int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_rs[i]) % rs_row_size[i];
        Bucket &bkt = rs[i][pos];

        if (bkt.id == sv)
        {
            return i;
        }
        if (!bkt.locked)
        {
            if (i == rs_level-1)
                return rs_level;
            return -1;  // conflicting
        }
	}
	return rs_level;
}

int
ReliableSketch::query_hash(int v)
{
    int sum = 0;
    if(mf_num_bkt) {
        sum += 2;
        int min_val = INT_MAX;
        for (register int j = 0; j < mf_n_hash; ++j) {
            int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_mf[j]) % mf_row_size + mf_row_size*j;
            int bkt = mf[pos];
            min_val = min(min_val, bkt);
        }
        if (min_val < mf_err_bound)
            return sum;  // filtered
    }

    int sv = (v & ID_MASK);
    for (int i = 0; i < rs_level; ++i) {
		int pos = MurmurHash3_x86_32((void*)&v, sizeof(int), rand_rs[i]) % rs_row_size[i];
        Bucket &bkt = rs[i][pos];
        sum += 1;

        if (bkt.id == sv || !bkt.locked)
        {
            return sum;
        }
	}
	return sum;
}

vector<PII>
ReliableSketch::query_heavyhitter(int threshold)
{
	vector<PII> ans;
    ans.clear();
    for (int i = 0; i < rs_level; ++i)
		for (int pos = 0; pos < rs_row_size[i]; ++pos) {
            Bucket &bkt = rs[i][pos];
            if (bkt.yes_cnt > 0) {
                int freq = query_freq(bkt.id);
                if (freq >= threshold) {
                    ans.push_back(mp(freq, int(bkt.id)));
                }
            }
        }
    return ans;
}

void
ReliableSketch::status()
{
    printf("total mem: %.2lfKB  mem ratio: %.2lf%%/%.2lf%%\n", total_mem/1024, mem_ratio*100, (1-mem_ratio)*100);
	printf("[MF Conf] bucket: %d   threshold: %d   hash: %d\n", mf_num_bkt, mf_err_bound, mf_n_hash);
	printf("[RS Conf] level: %d   bucket: %d(lvl.1 %d)   threshold(lvl.1): %d   R_w: %.2lf   R_l: %.2lf\n", rs_level, rs_num_bkt, rs_row_size[0], rs_err_bound[0], rs_r_w, rs_r_l);
}

void
ReliableSketch::print_mf_payload()
{
    printf("MF Payload [ ");
    if (mf_num_bkt)
    {
        int num_overflow = 0, num_bkt = mf_num_bkt;
        for (int j = 0; j < num_bkt; ++j)
            num_overflow += (mf[j] == mf_err_bound);
        printf("%.4lf(%d) ", (double)num_overflow / num_bkt, num_overflow);
    }
    printf("]\n");
}
