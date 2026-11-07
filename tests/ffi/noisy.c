#include <stdio.h>
int noisy(int v)
{
    printf("C says %d\n", v);
    return v + 1;
}
