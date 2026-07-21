process REVERSE {
    publishDir 'results', mode: 'copy'

    input:
    path listfile

    output:
    path 'reversed.txt'

    script:
    """
    tac ${listfile} > reversed.txt
    """
}

workflow {
    Channel.of('apple', 'banana', 'cherry')
        .collectFile(name: 'list.txt', newLine: true, sort: false)
        | REVERSE
}
