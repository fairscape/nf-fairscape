params.n = 8

// step 1: make a list of the first n letters, one per line
process MAKE_LIST {
    publishDir 'results', mode: 'copy'

    input:
    val n

    output:
    path 'letters.txt'

    script:
    """
    printf '%s\\n' {a..z} | head -n ${n} > letters.txt
    """
}

// step 2: reverse the list
process REVERSE {
    publishDir 'results', mode: 'copy'

    input:
    path letters

    output:
    path 'reversed.txt'

    script:
    """
    tac ${letters} > reversed.txt
    """
}

// step 3: divide the reversed list into two halves
process SPLIT_HALVES {
    publishDir 'results', mode: 'copy'

    input:
    path reversed

    output:
    path 'first_half.txt'
    path 'second_half.txt'

    script:
    """
    lines=\$(wc -l < ${reversed})
    half=\$(( (lines + 1) / 2 ))
    head -n \$half ${reversed} > first_half.txt
    tail -n +\$(( half + 1 )) ${reversed} > second_half.txt
    """
}

workflow {
    MAKE_LIST(params.n) | REVERSE | SPLIT_HALVES
}
