/* Concrete trace support. No task-specific invariants, assertions or bounds. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <limits.h>

_Static_assert(sizeof(int)==4 && sizeof(long)==4 && sizeof(void*)==4, "ILP32 required");
static unsigned char tr_defined[128];
static unsigned char *tr_masks[16];
static long long tr_lengths[16], tr_last[16];
static unsigned long long tr_writes[16], tr_visits[16], tr_steps, tr_choices;
static uint64_t tr_priorities[16][500], tr_rng, tr_seed, tr_choice_hash;
static unsigned long long tr_slots_visit[16][500];
static unsigned tr_counts[16], tr_budget, tr_window, tr_slot;
static unsigned long long tr_limit;
static char tr_buffer[16384];
static size_t tr_used;
static unsigned tr_loop;

static void tr_stop(const char *status) {
    printf("{\"event\":\"terminal\",\"status\":\"%s\",\"steps\":%llu,\"choices\":%llu,\"choice_hash\":\"%llu\"}\n",
        status, tr_steps, tr_choices, (unsigned long long)tr_choice_hash);
    fflush(stdout);
    exit(0);
}
static uint64_t tr_mix(uint64_t value) {
    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
    return value ^ (value >> 31);
}
static uint64_t tr_random(void) {
    tr_rng += UINT64_C(0x9e3779b97f4a7c15);
    return tr_mix(tr_rng);
}
static int tr_profile;
static long long tr_choice(int role) {
    uint64_t random = tr_random();
    long long value;
    if (role == 1) value = (random % 4) != 0;
    else if (role == 2 && tr_profile == 1) value = (long long)(random % 201) - 100;
    else if (role == 2 && tr_profile == 2) value = (long long)(random % 101);
    else switch (tr_profile) {
        case 0: value = 0; break;
        case 1: value = 1; break;
        case 2: value = 10; break;
        case 3: value = (long long)(random % 201) - 100; break;
        default: {
            const long long edges[] = {INT_MIN, -1, 0, 1, INT_MAX};
            value = edges[random % 5];
        }
    }
    tr_choices++;
    tr_choice_hash = tr_mix(tr_choice_hash ^ (uint64_t)value ^ tr_choices);
    return value;
}
static int tr_nd_int(int role) { return (int)tr_choice(role); }
static unsigned int tr_nd_uint(int role) { return (unsigned int)tr_choice(role); }
static unsigned char tr_nd_uchar(int role) { return (unsigned char)tr_choice(role); }

static void tr_append(const char *format, ...) {
    va_list args;
    va_start(args, format);
#ifdef _WIN32
    int count = _vsnprintf(tr_buffer + tr_used, sizeof(tr_buffer) - tr_used, format, args);
#else
    int count = vsnprintf(tr_buffer + tr_used, sizeof(tr_buffer) - tr_used, format, args);
#endif
    va_end(args);
    if (count < 0 || (size_t)count >= sizeof(tr_buffer) - tr_used) tr_stop("snapshot_buffer_exhausted");
    tr_used += (size_t)count;
}
static int tr_begin(unsigned loop) {
    unsigned long long visit = tr_visits[loop]++;
    if (++tr_steps > tr_limit) tr_stop("execution_step_budget");
    uint64_t priority = visit == 0 ? 0 : tr_mix(tr_seed ^ ((uint64_t)loop << 48) ^ visit) | 1;
    unsigned slot;
    if (tr_counts[loop] < tr_budget) slot = tr_counts[loop]++;
    else {
        slot = 0;
        for (unsigned i=1; i<tr_budget; i++) {
            if (tr_priorities[loop][i] > tr_priorities[loop][slot] ||
                (tr_priorities[loop][i] == tr_priorities[loop][slot] && tr_slots_visit[loop][i] > tr_slots_visit[loop][slot])) slot=i;
        }
        if (priority > tr_priorities[loop][slot] ||
            (priority == tr_priorities[loop][slot] && visit >= tr_slots_visit[loop][slot])) return 0;
    }
    tr_priorities[loop][slot] = priority;
    tr_slots_visit[loop][slot] = visit;
    tr_slot=slot; tr_loop=loop; tr_used=0;
    tr_append("{\"event\":\"observation\",\"loop_index\":\"%u\",\"slot\":%u,\"visit\":%llu,\"event_index\":%llu,\"state\":{\"__trace_start\":0", loop,slot,visit,tr_steps);
    return 1;
}
static int tr_scalar(const char *name, long long value) {
    tr_append(",\"%s\":%lld", name, value);
    return 0;
}
static void tr_init_array(unsigned tag, long long length) {
    if (length < 1 || length > 1000000) tr_stop("array_size_not_supported");
    free(tr_masks[tag]);
    tr_masks[tag] = calloc((size_t)length,1);
    if (!tr_masks[tag]) tr_stop("allocation_failure");
    tr_lengths[tag]=length; tr_last[tag]=-1;
}
static int tr_store(unsigned tag, int *array, long long index, int value) {
    if (index < 0 || index >= tr_lengths[tag]) tr_stop("array_access_violation");
    array[index]=value; tr_masks[tag][index]=1; tr_last[tag]=index; tr_writes[tag]++;
    return value;
}
static int tr_load(unsigned tag, const int *array, long long index) {
    if (index < 0 || index >= tr_lengths[tag]) tr_stop("array_access_violation");
    if (!tr_masks[tag][index]) tr_stop("uninitialized_array_read");
    return array[index];
}
static int tr_array(const char *name, unsigned tag, const int *array) {
    long long length=tr_lengths[tag];
    unsigned observed=0;
    tr_append(",\"%s\":{\"length\":%lld,\"writes\":%llu,\"cells\":{",name,length,tr_writes[tag]);
    for (long long i=0; i<length && i<(long long)tr_window; i++) if (tr_masks[tag][i]) {
        tr_append("%s\"%lld\":%d",observed ? ",":"",i,array[i]); observed++;
    }
    long long last=tr_last[tag];
    if (last >= (long long)tr_window && last < length && tr_masks[tag][last]) {
        tr_append("%s\"%lld\":%d",observed ? ",":"",last,array[last]); observed++;
    }
    tr_append("},\"complete\":%s}", observed==(unsigned long long)length ? "true":"false");
    return 0;
}
static int tr_end(void) {
    tr_append("}}");
    puts(tr_buffer); fflush(stdout);
    return 0;
}
static void tr_setup(unsigned seed, unsigned profile, unsigned budget, unsigned long long limit, unsigned window) {
    tr_seed=seed; tr_rng=seed; tr_profile=(int)profile; tr_budget=budget; tr_limit=limit; tr_window=window;
    setvbuf(stdout,NULL,_IONBF,0);
}
static void trace_assert(int cond) { if (!cond) tr_stop("assertion_failed"); }
static void trace_assume(int cond) { if (!cond) tr_stop("assumption_rejected"); }
