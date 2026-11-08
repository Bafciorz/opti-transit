package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.*;

import java.io.Serializable;

@Embeddable
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@EqualsAndHashCode
@ToString
public class StopTransferId implements Serializable {

    @Column(name = "from_stop_id", nullable = false)
    private String fromStopId;

    @Column(name = "to_stop_id", nullable = false)
    private String toStopId;
}
